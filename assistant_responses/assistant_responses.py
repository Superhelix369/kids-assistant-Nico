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

from ble_sender_pico import send_cmd  # ← これは worker の中だけで使う

import queue
import threading
from pathlib import Path

# =========================
# VoiceVox / OpenAI
# =========================
VOICEVOX_URL = config.VOICEVOX_URL
VOICEVOX_HOST = VOICEVOX_URL.split("//")[-1].split(":")[0] if VOICEVOX_URL else ""
print(f"✅ VoiceVox ホストとして '{VOICEVOX_HOST}' を使用します。")

client = openai.OpenAI(api_key=config.OPENAI_API_KEY)
BASE_DIR = Path(__file__).resolve().parent
PROMPT_FILE = BASE_DIR / "nico_prompt.txt"

NICO_INSTRUCTIONS = PROMPT_FILE.read_text(
    encoding="utf-8"
).strip()


# =========================
# 設定
# =========================
INACTIVITY_TIMEOUT = 1800
SAMPLERATE = 48000
DURATION = 5
FILENAME = "input.wav"
SPEAKER_ID = config.SPEAKER_ID

STOP_WORDS = ["stop", "ストップ", "すとっぷ", "Stop"]

IGNORE_WORDS = [
    "ご視聴ありがとうございました","ご清聴ありがとうございました",
    "最後まで視聴してくださって本当にありがとうございます",
    "おしまい。ご視聴ありがとうございました。",
    "字幕視聴ありがとうございました", "おやすみなさい",
    "最後まで視聴してくださって 本当にありがとうございます。"
]

GREETINGS = [
    "あそぼ！あそぼー！", "やったー！おしゃべりしよー", "やっほー！こんにちは",
    "ちょっとねむたいよー", "なに？なに？どうしたの？", "ねえねえ、こっちみてー",
    "どうしたの？", "よく寝た～!", "わーい！わーい！おねえちゃん！", "おなかしゅいた"
]

GOOD_WORDS = [
    "大好き", "ありがとう", "うれしい", "やった", "楽しい", "ねえね",
    "すごい", "わーい", "うれし", "だいすき", "だいしゅき",
    "幸せ", "しあわせ", "ありがと～","うれちい","たのしい",
]

def pick_input_device():
    keywords = ["UACDemoV1.0", "USB Audio"]
    for i, d in enumerate(sd.query_devices()):
        if d["max_input_channels"] >= 1 and any(k in d["name"] for k in keywords):
            print(f"✅ Selected INPUT_DEVICE={i}: {d['name']}")
            return i
    raise RuntimeError("❌ USB mic (UACDemoV1.0 / USB Audio) not found")

INPUT_DEVICE = pick_input_device()
print("🎤 Using input:", sd.query_devices(INPUT_DEVICE, 'input'))
sd.default.device = (INPUT_DEVICE, None)
sd.default.samplerate = SAMPLERATE
sd.default.channels = 1

# =========================
# BLE送信 1本化（対策②）
# =========================
ble_queue = queue.Queue()

def ble_worker():
    """BLE送信はこのスレッド1本だけが担当する（同時接続事故を防ぐ）"""
    while True:
        cmd = ble_queue.get()
        try:
            # ここだけが send_cmd を呼ぶ
            send_cmd(cmd)
            print(f"📤 BLE送信(worker): {cmd}")
        except Exception as e:
            print(f"⚠ BLE送信(worker)失敗: {cmd} / {e}")
        finally:
            ble_queue.task_done()

threading.Thread(target=ble_worker, daemon=True).start()

def ble_send(cmd: str):
    """キューに積むだけ（呼び出し側は絶対にsend_cmdしない）"""
    ble_queue.put(cmd)


# =========================
# 動作中フラグ（対策③）
# =========================
is_moving_lock = threading.Lock()
is_moving = False


# =========================
# Motor Control（finally STOP保証 + 送信1本化）
# =========================
def nico_action_greeting():
    try:
        ble_send("FORWARD:1.5")
        time.sleep(1.7)
    finally:
        ble_send("STOP")


def nico_action_goodword():
    """
    良い言葉で喜びダンス：前進2s → stop0.5s → 後退2s → stop
    ・同時実行禁止（is_moving）
    ・例外でも最後にSTOP（対策①）
    """
    global is_moving

    # --- 連打防止 ---
    with is_moving_lock:
        if is_moving:
            return
        is_moving = True

    try:
        ble_send("FORWARD:2.0")  # Picoが1.2秒動かして自動STOP
        time.sleep(2.1)         # 次コマンド間の余裕（0でもOK）
        ble_send("REVERSE:2.0")  # Picoが1.2秒動かして自動STOP
        time.sleep(2.1)
        
    except Exception as e:
        print("⚠ 動作中エラー:", e)

    finally:
        ble_send("STOP")
        with is_moving_lock:
            is_moving = False


# =========================
# 音声
# =========================
def record_audio():
    try:
        if os.path.exists(FILENAME):
            os.remove(FILENAME)

        audio = sd.rec(
            int(SAMPLERATE * DURATION),
            dtype='int16'
        )
        sd.wait()

        # (frames,1) → (frames,) にして保存（安全）
        wav.write(FILENAME, SAMPLERATE, audio.reshape(-1))

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
    except:
        return ""


# =========================
# Responses API
# =========================
def get_assistant_response(previous_response_id, user_input):
    """
    Responses APIで返答を生成する。

    previous_response_id:
        前回のResponse ID。
        初回はNone。2回目以降は会話継続に使用する。
    """
    try:
        request_params = {
            "model": config.OPENAI_MODEL,
            "instructions": NICO_INSTRUCTIONS,
            "input": user_input,
        }

        # 2回目以降のみ、前回のResponse IDを指定する
        if previous_response_id:
            request_params["previous_response_id"] = previous_response_id

        response = client.responses.create(**request_params)

        reply = response.output_text.strip()

        if not reply:
            print("⚠ Responses APIから返答テキストがありません。")
            return "うまく答えられなかったよ。", previous_response_id

        # プロンプト指示だけで50文字を超えた場合の安全対策
        if len(reply) > 50:
            reply = reply[:50]

        return reply, response.id

    except Exception as e:
        print("❌ Responses API エラー:", e)
        return "うまく答えられなかったよ。", previous_response_id


# =========================
# VoiceVox
# =========================
def synthesize_voice(text, speaker):
    try:
        params = {"text": text, "speaker": speaker}
        query_res = requests.post(f"{VOICEVOX_URL}/audio_query", params=params)
        query = query_res.json()
        query.update({
            "speedScale": 1.1,
            "intonationScale": 1.6,
            "pitchScale": 0,
            "volumeScale": 1.0,
        })
        synth_res = requests.post(
            f"{VOICEVOX_URL}/synthesis", params=params, json=query
        )
        return synth_res.content
    except:
        return None


def play_audio(audio_data, factor=7.0):
    # 先頭に無音を追加して、Bluetoothスピーカーの立ち上がり遅延を吸収する
    LEADING_SILENCE_SEC = 0.6

    amplified = np.frombuffer(audio_data, dtype=np.int16)
    amplified = (amplified * factor).clip(-32768, 32767).astype(np.int16)

    silence = np.zeros(int(24000 * LEADING_SILENCE_SEC), dtype=np.int16)

    # 無音 + 本編音声
    output = np.concatenate([silence, amplified])
    data = output.tobytes()

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        tmp.write(data)
        tmp_path = tmp.name

    subprocess.run(["aplay", "-r", "24000", "-f", "S16_LE", "-c", "1", tmp_path])

# =========================
# 会話処理
# =========================
def speak_greeting():
    greeting = random.choice(GREETINGS)
    print(f"🎙️ Greeting: {greeting}")

    # ★ 挨拶動作（しゃべる直前に開始）
    threading.Thread(target=nico_action_greeting, daemon=True).start()

    audio = synthesize_voice(greeting, SPEAKER_ID)
    if audio:
        play_audio(audio)


def speak_response(text):
    print(f"🤖 ニコ: {text}")

    audio = synthesize_voice(text, SPEAKER_ID)
    if not audio:
        return

    # ★ 良い言葉を検出したら「しゃべりながら」動かす
    if any(word in text for word in GOOD_WORDS):
        threading.Thread(target=nico_action_goodword, daemon=True).start()

    play_audio(audio)


# =========================
# メインループ
# =========================
def listen_and_talk_loop():
    last_valid_input_time = time.time()

    speak_greeting()
    previous_response_id = None

    while True:
        if not record_audio():
            continue

        text = transcribe_audio()
        now = time.time()

        if os.path.exists(FILENAME):
            os.remove(FILENAME)

        if not text:
            print("(無音)")
        elif any(w in text for w in IGNORE_WORDS):
            print("(無視ワード)")
        else:
            print(f"📝 子供: {text}")

            if any(s in text for s in STOP_WORDS):
                speak_response("楽しかった！またあそんでね！")
                print("STOP")
                sys.exit(0)

            reply, previous_response_id = get_assistant_response(
                previous_response_id,
                text
            )
            speak_response(reply)
            last_valid_input_time = now

        if now - last_valid_input_time > INACTIVITY_TIMEOUT:
            speak_response("楽しかった！またあそんでね！")
            print("STOP")
            sys.exit(0)

        time.sleep(0.3)


# =========================
# 起動
# =========================
if __name__ == "__main__":
    try:
        listen_and_talk_loop()
    except KeyboardInterrupt:
        print("🛑 終了")
    finally:
        # 最後に念のためSTOPを積んで終わる（安全）
        try:
            ble_send("STOP")
        except:
            pass
        sys.exit(0)
