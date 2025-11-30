import sounddevice as sd
import numpy as np
import scipy.io.wavfile as wav
import openai
import time
import os
import tempfile
import requests
import random
import sys
import subprocess
import config


# VoiceVox のホスト（Elastic IP）を .env から取得
VOICEVOX_URL = config.VOICEVOX_URL
VOICEVOX_PORT = config.VOICEVOX_PORT
VOICEVOX_HOST = VOICEVOX_URL.split("//")[-1].split(":")[0] if VOICEVOX_URL else ""
print(f"✅ VoiceVox ホストとして '{VOICEVOX_HOST}' を使用します。")


# OpenAI クライアント初期化
client = openai.OpenAI(api_key=config.OPENAI_API_KEY)

# 設定
INACTIVITY_TIMEOUT = 300
SAMPLERATE = 48000
DURATION = 5
FILENAME = "input.wav"
SPEAKER_ID = config.SPEAKER_ID
INPUT_DEVICE = 0  # ← 適切な入力デバイス番号に変更（sounddevice -m で確認済み）


STOP_WORDS = ["stop", "ストップ", "すとっぷ", "Stop"]
IGNORE_WORDS = [
    "ご視聴ありがとうございました", "ご清聴ありがとうございました",
    "最後まで視聴してくださって本当にありがとうございます",
    "本日はご視聴ありがとうございました",
    "ご視聴ありがとうございました！", "ご視聴ありがとうございました!",
    "🐯 Sound Hodori 사운드 호도리 サウンドゥ ホドリ",
    "おしまい。ご視聴ありがとうございました。",
    "最後まで視聴してくださって 本当にありがとうございます。",
    "最後までご視聴いただきありがとうございいます。",
    "ダメです",
    "字幕視聴ありがとうございました",
    "おやすみなさい。",
    "おやすみなさい",
    "また会いましょう。"    
]

GREETINGS = [
    "あそぼ！あそぼー！", "やったー！おしゃべりしよー", "やっほー！こんにちは",
    "ちょっとねむたいよー", "なに？なに？どうしたの？", "ねえねえ、こっちみてー",
    "どうしたの？", "よく寝た～!", "わーい！わーい！おねえちゃん！", "おなかしゅいた"
]


# デフォルトデバイスを設定（省略も可だが念のため）
sd.default.device = (INPUT_DEVICE, None)


# ------------------ 音声関連 ------------------ #

def record_audio():
    try:
        if os.path.exists(FILENAME):
            os.remove(FILENAME)

        audio = sd.rec(
            int(SAMPLERATE * DURATION),
            samplerate=SAMPLERATE,
            channels=1,
            dtype=np.int16,
            device=INPUT_DEVICE
        )
        sd.wait()
        wav.write(FILENAME, SAMPLERATE, audio)
        return True
    except Exception as e:
        print("❌ 録音エラー:", e)
        return False


def transcribe_audio():
    try:
        with open(FILENAME, "rb") as f:
            response = client.audio.transcriptions.create(
                model="whisper-1", file=f, language="ja", temperature=0.0
            )
        return response.text.strip()
    except Exception as e:
        print("❌ 音声認識エラー:", e)
        return ""

# ------------------ Assistant API ------------------ #

def create_assistant_thread():
    return client.beta.threads.create().id

def get_assistant_response(thread_id, user_input):
    try:
        client.beta.threads.messages.create(thread_id=thread_id, role="user", content=user_input)
        run = client.beta.threads.runs.create(thread_id=thread_id, assistant_id=config.ASSISTANT_ID)

        while True:
            run_status = client.beta.threads.runs.retrieve(thread_id=thread_id, run_id=run.id)
            if run_status.status == "completed":
                break
            time.sleep(0.5)

        messages = client.beta.threads.messages.list(thread_id=thread_id)
        return messages.data[0].content[0].text.value
    except Exception as e:
        print("❌ Assistant API エラー:", e)
        return "エラーが発生しました。"

# ------------------ Voicevox ------------------ #

def synthesize_voice(text, speaker):
    try:
        params = {"text": text, "speaker": speaker}
        query_res = requests.post(f"{VOICEVOX_URL}/audio_query", params=params)
        if query_res.status_code != 200:
            print("❌ クエリ作成失敗:", query_res.text)
            return None
        query = query_res.json()
        query.update({
            "speedScale": 1.1,
            "intonationScale": 1.6,
            "pitchScale": 0,
            "volumeScale": 1.0,
            "prePhonemeLength": 0,
            "postPhonemeLength": 0
        })
        synth_res = requests.post(f"{VOICEVOX_URL}/synthesis", params=params, json=query)
        if synth_res.status_code != 200:
            print("❌ 音声合成失敗:", synth_res.text)
            return None
        return synth_res.content
    except Exception as e:
        print("❌ Voicevoxエラー:", e)
        return None

def amplify_audio(audio_data, factor):
    audio_array = np.frombuffer(audio_data, dtype=np.int16)
    amplified = (audio_array * factor).clip(-32768, 32767).astype(np.int16)
    return amplified.tobytes()

def play_audio(audio_data, factor=1.5):
    amplified = amplify_audio(audio_data, factor)

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        tmp.write(amplified)
        tmp_path = tmp.name

    subprocess.run(["aplay", tmp_path])



# ------------------ メイン会話ループ ------------------ #

def speak_greeting():
    greeting = random.choice(GREETINGS)
    print(f"🎙️ あいさつ: {greeting}")
    audio = synthesize_voice(greeting, SPEAKER_ID)
    if audio:
        play_audio(audio)

def speak_response(text):
    print(f"🤖 ニコ: {text}")
    audio = synthesize_voice(text, SPEAKER_ID)
    if audio:
        play_audio(audio)
    else:
        print("⚠ 音声再生スキップ（音声合成失敗）")

def listen_and_talk_loop():
    last_valid_input_time = time.time()
    speak_greeting()
    thread_id = create_assistant_thread()

    while True:
        if not record_audio():
            time.sleep(1)
            continue

        text = transcribe_audio()
        now = time.time()

        if os.path.exists(FILENAME):
            os.remove(FILENAME)

        if not text:
            print("（無音認識）")
        elif any(word in text for word in IGNORE_WORDS):
            print("（無視ワード検出）")
        else:
            print(f"📝 ユーザー: {text}")
            if any(stop_word in text for stop_word in STOP_WORDS):
                print("👋 『ストップ』を検出。会話を終了します。")
                speak_response("またね")
                print("STOP")
                sys.exit(0)
            reply = get_assistant_response(thread_id, text)
            if not reply:
                reply = "うまく答えられなかったよ。もう一回言ってみて？"
            speak_response(reply)
            last_valid_input_time = now

        if now - last_valid_input_time > INACTIVITY_TIMEOUT:
            print("⏳ 300秒間有効な会話がありませんでした。終了します。")
            speak_response("またね")
            print("STOP")
            sys.exit(0)
            # 子供が「ストップ」と言ったら、assistantを終了（main.py側で監視されている）


        time.sleep(0.5) #おそらくCPU稼働率を下げるため？でも必要ないかも。ない方が応答速度向上？

if __name__ == "__main__":
    try:
        listen_and_talk_loop()
    except KeyboardInterrupt:
        print("🛑 キーボード割り込みにより終了")
    except Exception as e:
        print(f"❌ 未処理の例外:", e)
    finally:
        print("🧹 assistant.py を終了します")
        sys.exit(0)

