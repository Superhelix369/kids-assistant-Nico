import requests
import random
import sys
import os
import time
import subprocess
import tempfile
import numpy as np
from config import VOICEVOX_URL, SPEAKER_ID

greetings = [
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

def amplify_audio(audio_data, amplification_factor):
    """音声データの音量を増幅"""
    audio_array = np.frombuffer(audio_data, dtype=np.int16)
    amplified_audio = (audio_array * amplification_factor).clip(-32768, 32767).astype(np.int16)
    return amplified_audio.tobytes()

def play_audio(audio_data, amplification_factor=4.5):
    """音量調整した音声を一時WAVとして保存して再生（Bluetooth対応）"""
    amplified_data = amplify_audio(audio_data, amplification_factor)

    # 一時ファイルを作成
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        tmp.write(amplified_data)
        tmp_path = tmp.name

    # フォーマット指定で再生（PulseAudio変換を回避）
    subprocess.run(["aplay", "-r", "24000", "-f", "S16_LE", "-c", "1", tmp_path])

def speak(text):
    # クエリ作成
    res = requests.post(
        f"{VOICEVOX_URL}/audio_query",
        params={"text": text, "speaker": SPEAKER_ID}
    )
    res.raise_for_status()
    query = res.json()

    # 幼児っぽい話し方にチューニング
    query["speedScale"] = 1.1
    query["intonationScale"] = 2.0
    query["pitchScale"] = 0
    query["volumeScale"] = 1.2
    query["prePhonemeLength"] = 0
    query["postPhonemeLength"] = 0

    # 音声合成
    res = requests.post(
        f"{VOICEVOX_URL}/synthesis",
        params={"speaker": SPEAKER_ID},
        json=query
    )
    res.raise_for_status()
    audio_data = res.content

    # 再生
    play_audio(audio_data)

def main():
    if len(sys.argv) < 2:
        print("EC2ホスト名またはIPを指定してください。")
        return
    time.sleep(1)  # EC2起動後のVoicevox安定待ち

    greeting = random.choice(greetings)
    print(f"🎙️ 再生メッセージ: {greeting}")
    speak(greeting)

if __name__ == "__main__":
    main()
