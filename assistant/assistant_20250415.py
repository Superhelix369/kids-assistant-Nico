import sounddevice as sd
import numpy as np
import scipy.io.wavfile as wav
import openai
import time
import os
import requests
import simpleaudio as sa
import random
import sys

from config import OPENAI_API_KEY, ASSISTANT_ID

# OpenAI クライアント初期化
client = openai.OpenAI(api_key=OPENAI_API_KEY)

# 設定
INACTIVITY_TIMEOUT = 300  # 300秒（5分）
SAMPLERATE = 16000
DURATION = 5
FILENAME = "input.wav"
SPEAKER_ID = config.SPEAKER_ID

# Voicevox ホスト設定
VOICEVOX_PORT = config.VOICEVOX_PORT
VOICEVOX_HOST = sys.argv[config.SPEAKER_ID] if len(sys.argv) > config.SPEAKER_ID else "localhost"
VOICEVOX_URL = f"http://{VOICEVOX_HOST}:{VOICEVOX_PORT}"

STOP_WORDS = ["stop", "ストップ", "すとっぷ", "Stop"]
IGNORE_WORDS = [
    "ご視聴ありがとうございました", 
    "ご視聴ありがとうございました。",
    "ご清聴ありがとうございました", 
    "最後まで視聴してくださって本当にありがとうございます",
    "本日はご視聴ありがとうございました",
    "ご視聴ありがとうございました！",
    "ご視聴ありがとうございました!",
    "🐯 Sound Hodori 사운드 호도리 サウンドゥ ホドリ",
    "おしまい。ご視聴ありがとうございました。",
    "最後まで視聴してくださって 本当にありがとうございます。"
]

GREETINGS = [
    "あそぼ！あそぼー！",
    "やったー！おしゃべりしよー",
    "やっほー！こんにちは",
    "ちょっとねむたいよー",
    "ん？なに？なに？",
    "ねえねえ、こっちみてー",
    "どうしたの？",
    "う～ん！よく寝た～",
    "わーい！わーい！おねえちゃん！",
    "おなかしゅいた"
]

# ------------------ 音声関連 ------------------ #

def record_audio():
    try:
        audio = sd.rec(int(SAMPLERATE * DURATION), samplerate=SAMPLERATE, channels=config.SPEAKER_ID, dtype=np.int16)
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
                model="whisper-config.SPEAKER_ID", file=f, language="ja", temperature=0.0
            )
        text = response.text.strip()
        return text
    except Exception as e:
        print("❌ 音声認識エラー:", e)
        return ""

# ------------------ Assistant API ------------------ #

def create_assistant_thread():
    thread = client.beta.threads.create()
    return thread.id

def get_assistant_response(thread_id, user_input):
    try:
        client.beta.threads.messages.create(thread_id=thread_id, role="user", content=user_input)
        run = client.beta.threads.runs.create(thread_id=thread_id, assistant_id=ASSISTANT_ID)
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
            "intonationScale": 2.0,
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

def play_audio(audio_data, factor=4.5):
    amplified = amplify_audio(audio_data, factor)
    wave = sa.WaveObject(amplified, num_channels=config.SPEAKER_ID, bytes_per_sample=2, sample_rate=24000)
    play = wave.play()
    play.wait_done()

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

def listen_and_talk_loop():
    last_valid_input_time = time.time()
    speak_greeting()
    thread_id = create_assistant_thread()

    while True:
        if not record_audio():
            continue

        text = transcribe_audio()
        now = time.time()

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
            speak_response(reply)
            last_valid_input_time = now

        if now - last_valid_input_time > INACTIVITY_TIMEOUT:
            print("⏳ 300秒間有効な会話がありませんでした。終了します。")
            speak_response("またね")
            print("STOP")
            sys.exit(0)

        time.sleep(config.SPEAKER_ID)

if __name__ == "__main__":
    listen_and_talk_loop()