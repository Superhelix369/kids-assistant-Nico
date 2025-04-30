import config
import boto3
import time
import subprocess
import paramiko
from gpiozero import Button, LED, Device
from gpiozero.pins.lgpio import LGPIOFactory
import socket
import requests
import sys
import os
import threading
import traceback
import atexit
from enum import Enum

# ======= ユーザー設定 =======
INSTANCE_ID = config.INSTANCE_ID
REGION = config.AWS_REGION
VOICEVOX_PORT = config.VOICEVOX_PORT
EC2_HOST = config.EC2_HOST
ASSISTANT_SCRIPT = 'assistant.py'
SSH_KEY_PATH = config.SSH_KEY_PATH
# ============================

# ハードウェア設定
Device.pin_factory = LGPIOFactory()

button = Button(17, pull_up=True, bounce_time=0.1)
led = LED(18)

def cleanup_gpio():
    print("🧹 GPIO解放中...")
    try:
        button.when_pressed = None
        led.close()
        button.close()
        print("🔓 gpiozero による GPIO 解放OK")
    except Exception as e:
        print("⚠ GPIO解放エラー:", e)

atexit.register(cleanup_gpio)

# AWS クライアント
ec2 = boto3.client('ec2', region_name=REGION)

class Mode(Enum):
    IDLE = config.SPEAKER_ID
    STARTING = 2
    TALKING = 3
    SHUTTING_DOWN = 4

state = {
    "mode": Mode.IDLE,
    "host": EC2_HOST,
    "assistant_process": None,
}

def wait_for_ssh_ready(host, port=22, timeout=60):
    print(f"SSHポート {host}:{port} を待機中...")
    start_time = time.time()
    while time.time() - start_time < timeout:
        try:
            with socket.create_connection((host, port), timeout=5):
                print("SSHポートに接続成功！")
                return True
        except:
            time.sleep(2)
    print("⏰ SSH接続タイムアウト")
    return False

def wait_until_ec2_stopped(instance_id):
    print("⏳ EC2の停止完了を待っています...")
    while True:
        response = ec2.describe_instances(InstanceIds=[instance_id])
        instance_state = response["Reservations"][0]["Instances"][0]["State"]["Name"]
        print(f"📦 現在の状態: {instance_state}")
        if instance_state == "stopped":
            print("✅停止完了")
            break
        elif instance_state == "terminated":
            raise RuntimeError("❌ インスタンスが terminate されています。")
        time.sleep(5)

def start_ec2():
    print("▶ EC2インスタンスを起動します...")
    response = ec2.describe_instances(InstanceIds=[INSTANCE_ID])
    instance = response["Reservations"][0]["Instances"][0]
    current_state = instance["State"]["Name"]
    print(f"🔎 EC2の現在の状態: {current_state}")

    if current_state == "running":
        print("⚠ すでに起動中です。")
    elif current_state == "stopped":
        ec2.start_instances(InstanceIds=[INSTANCE_ID])
    elif current_state == "stopping":
        wait_until_ec2_stopped(INSTANCE_ID)
        ec2.start_instances(InstanceIds=[INSTANCE_ID])
    else:
        raise RuntimeError(f"⚠ 起動できない状態: {current_state}")

    print("⏳ EC2の起動を待機します...")
    waiter = ec2.get_waiter("instance_running")
    waiter.wait(InstanceIds=[INSTANCE_ID])
    print("✅ EC2起動完了！")

    if wait_for_ssh_ready(EC2_HOST):
        return EC2_HOST
    else:
        print("❌ SSH接続できませんでした")
        return None

# ⭐ VoiceVox の起動確認だけは必要
def wait_for_voicevox(host, port=VOICEVOX_PORT, timeout=60):
    print(f"🔄 VoiceVox 起動確認中: http://{host}:{port}")
    start_time = time.time()

    while time.time() - start_time < timeout:
        try:
            with socket.create_connection((host, port), timeout=3):
                try:
                    response = requests.get(f"http://{host}:{port}", timeout=3)
                    if response.status_code == 200:
                        speakers_response = requests.get(f"http://{host}:{port}/speakers", timeout=3)
                        if speakers_response.status_code == 200:
                            print("✅ VoiceVox /speakers API 応答あり → 完全起動確認！")
                            return True
                        else:
                            print(f"⏳ /speakers応答異常: {speakers_response.status_code}")
                    else:
                        print(f"⏳ HTTP応答コード異常: {response.status_code}")
                except requests.exceptions.RequestException as e:
                    print(f"⏳ HTTP応答待ち中… {e}")
        except Exception as e:
            print(f"⏳ ポート接続待ち中… {e}")
        time.sleep(2)

    print("❌ VoiceVox 起動タイムアウト")
    return False


def stop_voicevox(host):
    try:
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh.connect(host, username='ubuntu', key_filename=SSH_KEY_PATH)
        ssh.exec_command("docker stop voicevox")
        ssh.close()
    except Exception as e:
        print("⚠Voicevox停止失敗:", e)

def stop_ec2():
    try:
        ec2.stop_instances(InstanceIds=[INSTANCE_ID])
        print("✅EC2インスタンスを停止指示完了")
    except Exception as e:
        print("⚠EC2停止エラー:", e)

def monitor_assistant():
    proc = state["assistant_process"]
    if proc is None:
        return
    try:
        proc.wait(timeout=900)  # ⏱ 15分間のタイムアウト
        print("🛑 assistant.py が正常終了しました（またはタイムアウト）")
    except subprocess.TimeoutExpired:
        print("⏰ assistant.py がタイムアウトしました → 強制シャットダウンします")
        proc.kill()
    finally:
        handle_shutdown()


def start_assistant(host):
    print("🧠 assistant.py を起動します")
    led.on()
    try:
        proc = subprocess.Popen(["python3", ASSISTANT_SCRIPT, host])
        state["assistant_process"] = proc
        threading.Thread(target=monitor_assistant, daemon=True).start()
    except Exception as e:
        print("⚠ assistant.py の起動に失敗:", e)
        traceback.print_exc()
        handle_shutdown()


def stop_assistant():
    proc = state.get("assistant_process")
    if proc and proc.poll() is None:
        print("assistant.py を終了させます...")
        proc.terminate()
        try:
            proc.wait(timeout=5)
            print("✅ assistant.py 終了成功")
        except subprocess.TimeoutExpired:
            print("⚠ assistant.py 応答なし → 強制終了")
            proc.kill()
    state["assistant_process"] = None

def stop_pi():
    print("🛑 Raspberry Pi をシャットダウンします...")
    
    # GPIOを明示的に解放
    #cleanup_gpio()
    
    # テスト用：本番運用時にコメントアウト解除
    #os.system("sudo shutdown -h now")
# グローバル変数として定義（ファイルの冒頭や handle_shutdown() の外）


shutdown_initiated = False

def handle_shutdown():
    global shutdown_initiated
    if shutdown_initiated:
        print("⚠️ シャットダウン処理はすでに実行されています。スキップします。")
        return
    shutdown_initiated = True
    print("⚫ シャットダウン処理中...")
    state["mode"] = Mode.SHUTTING_DOWN
    stop_assistant()
    if state["host"]:
        # stop_voicevox(state["host"])  # ← 削除済みでOK
        stop_ec2()
        state["host"] = None
    try:
        led.off()
    except Exception as e:
        print(f"⚠️ LED.off() でエラー: {e}")
    state["mode"] = Mode.IDLE
    print("✅ シャットダウン完了。Raspberry Pi をシャットダウンします。")

    cleanup_gpio()
    
    # === 本番時に以下を有効化無効か処理をする ===
    
    # stop_pi()  # 本番時に有効化
    sys.exit(0)  # テスト用(本番はコメントアウト。Raspberry Pi をシャットダウンせずに、スクリプトだけ終了させるため)

def play_button_prompt():
    print("🔈『ボタンを押してね』の音声を再生します...")
    try:
        subprocess.run(["aplay", "/home/pi/audio/button_instructions.wav"], check=True)
        print("✅ 音声再生完了")
    except Exception as e:
        print("⚠ 音声再生エラー:", e)


def on_button_pressed():
    mode = state["mode"]
    print(f"🔘 ボタンが押されました（現在のモード: {mode}）")

    if mode == Mode.IDLE:
        state["mode"] = Mode.STARTING
        host = start_ec2()
        if host and wait_for_voicevox(host):  # ⭐ start_voicevox()は呼ばない
            state["host"] = host
            state["mode"] = Mode.TALKING
            start_assistant(host)
        else:
            print("⚠ 初期化失敗。IDLEに戻ります。")
            state["mode"] = Mode.IDLE
            led.off()
    elif mode == Mode.TALKING:
        handle_shutdown()
    else:
        print("⚠ 処理中です。ボタン操作は無効です。")

try:
    play_button_prompt()  # ← ここで音声案内
    button.when_pressed = on_button_pressed
    print("✨ ボタン操作を待っています...")
    while True:
        time.sleep(config.SPEAKER_ID)
except KeyboardInterrupt:
    print("🧹 Ctrl+C による終了検知")
except Exception as e:
    print("❌ 致命的なエラー発生:", e)
    traceback.print_exc()
    handle_shutdown()
finally:
    cleanup_gpio()