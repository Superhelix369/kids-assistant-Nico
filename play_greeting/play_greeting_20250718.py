import requests
import random
import sys
import os
import time
import simpleaudio as sa
import numpy as np
from config import OPENAI_API_KEY, ASSISTANT_ID, VOICEVOX_URL, SPEAKER_ID


VOICEVOX_URL = "http://xxxxxxxxxxxxxxxxconfig.VOICEVOX_PORT"
speaker_id = config.SPEAKER_ID  # ずんだもん（ノーマル）

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
    """音声を再生 (音量調整付き)"""
    amplified_data = amplify_audio(audio_data, amplification_factor)
    wave_obj = sa.WaveObject(amplified_data, num_channels=config.SPEAKER_ID, bytes_per_sample=2, sample_rate=24000)
    play_obj = wave_obj.play()
    play_obj.wait_done()

def speak(text):
    # クエリ作成
    res = requests.post(
        f"{VOICEVOX_URL}/audio_query",
        params={"text": text, "speaker": speaker_id}
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
        params={"speaker": speaker_id},
        json=query
    )
    res.raise_for_status()
    audio_data = res.content

    # 再生（音量増幅付き）
    play_audio(audio_data)

def main():
    if len(sys.argv) < 2:
        print("EC2ホスト名またはIPを指定してください。")
        return
    time.sleep(config.SPEAKER_ID)  # EC2起動後のVoicevox安定待ち（念のため）

    greeting = random.choice(greetings)
    print(f"🎙️ 再生メッセージ: {greeting}")
    speak(greeting)

if __name__ == "__main__":
    main()
