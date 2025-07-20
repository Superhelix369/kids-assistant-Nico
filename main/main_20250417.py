import config
import boto3
import time
import subprocess
import paramiko
from gpiozero import Button, LED
from signal import pause
import socket
import signal
import sys
import lgpio
import os
import threading
from enum import Enum

# ======= ユーザー設定 =======
INSTANCE_ID = config.INSTANCE_ID
REGION = config.AWS_REGION
VOICEVOX_PORT = config.VOICEVOX_PORT
ASSISTANT_SCRIPT = 'assistant.py'
SSH_KEY_PATH = config.SSH_KEY_PATH
# ============================


# ハードウェア設定
button = Button(17, pull_up=True, bounce_time=0.1)
led = LED(18)

def cleanup_gpio():
    print("🧹 GPIO解放中...")
    led.close()
    button.close()
    try:
        import lgpio
        h = lgpio.gpiochip_open(0)
        lgpio.gpio_free(h, 17)  # GPIO17
        lgpio.gpio_free(h, 18)  # GPIO18 (LED用も入れるなら)
        lgpio.gpiochip_close(h)
        print("🔓 GPIO強制解放OK！")
    except Exception as e:
        print("⚠ GPIO解放エラー:", e)
    

def handle_exit(signum, frame):
    cleanup_gpio()
    sys.exit(0)

# シグナルを捕まえて終了時にGPIOを解放
signal.signal(signal.SIGINT, handle_exit)   # Ctrl+C
signal.signal(signal.SIGTERM, handle_exit)  # プロセス終了


# AWS クライアント
ec2 = boto3.client('ec2', region_name=REGION)

# モード定義
class Mode(Enum):
    IDLE = config.SPEAKER_ID
    STARTING = 2
    TALKING = 3
    SHUTTING_DOWN = 4

# 状態管理
state = {
    "mode": Mode.IDLE,
    "host": None,
    "assistant_process": None,
}

# ==== EC2関連 ====

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
    ec2 = boto3.client("ec2", region_name=config.AWS_REGION)
    print("⏳ EC2の停止完了を待っています...")
    while True:
        response = ec2.describe_instances(InstanceIds=[instance_id])
        state = response["Reservations"][0]["Instances"][0]["State"]["Name"]
        print(f"📦 現在の状態: {state}")
        if state == "stopped":
            print("✅停止完了")
            break
        elif state == "terminated":
            raise RuntimeError("❌ インスタンスが terminate されています。")
        time.sleep(5)
        
def start_ec2():
    print("▶ EC2インスタンスを起動します...")
    ec2 = boto3.client("ec2", region_name=config.AWS_REGION)

    # まず現在の状態を確認
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

    # 起動後の最新情報を取得し、host名取得
    response = ec2.describe_instances(InstanceIds=[INSTANCE_ID])
    instance = response["Reservations"][0]["Instances"][0]
    host = instance["PublicDnsName"]
    print(f"🌐 パブリックDNS: {host}")

    # SSH接続可能になるまで待機
    if wait_for_ssh_ready(host):
        return host
    else:
        print("❌ SSH接続できませんでした")
        return None



# ==== Voicevox関連 ====
def start_voicevox(host):
    try:
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh.connect(host, username='ubuntu', key_filename=SSH_KEY_PATH)

        stdin, stdout, _ = ssh.exec_command("docker ps -q -f name=voicevox")
        if stdout.read().strip():
            print("Voicevoxはすでに起動しています。")
            ssh.close()
            return True

        stdin, stdout, _ = ssh.exec_command("docker images -q voicevox/voicevox_engine:cpu-ubuntu20.04-latest")
        if not stdout.read().strip():
            print("Voicevoxイメージが存在しません。pull中...")
            ssh.exec_command("docker pull voicevox/voicevox_engine:cpu-ubuntu20.04-latest")
            time.sleep(30)

        ssh.exec_command("docker run -d --rm --name voicevox -p config.VOICEVOX_PORT:config.VOICEVOX_PORT --pull=never voicevox/voicevox_engine:cpu-ubuntu20.04-latest")
        print("Voicevoxを起動しました。")
        ssh.close()
        return wait_for_voicevox(host)
    except Exception as e:
        print("❌ Voicevox起動失敗:", e)
        return False

def wait_for_voicevox(host):
    for _ in range(30):
        try:
            with socket.create_connection((host, VOICEVOX_PORT), timeout=3):
                print("✅ Voicevoxが利用可能になりました。")
                return True
        except:
            time.sleep(2)
    print("⏰ Voicevox接続タイムアウト")
    return False

def stop_voicevox(host):
    try:
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh.connect(host, username='ubuntu', key_filename=SSH_KEY_PATH)
        ssh.exec_command("docker stop voicevox")
        ssh.close()
    except Exception as e:
        print("Voicevox停止失敗:", e)

def stop_ec2():
    try:
        ec2.stop_instances(InstanceIds=[INSTANCE_ID])
        print("EC2インスタンスを停止しました。")
    except Exception as e:
        print("EC2停止エラー:", e)

# ==== Assistant ====
def monitor_assistant():
    proc = state["assistant_process"]
    if proc is None:
        return
    proc.wait()
    print("🛑 assistant.py が終了しました（自動検知）")
    handle_shutdown()  # ←ここでEC2停止やPiシャットダウンへ
    
def start_assistant(host):
    print("🧠 assistant.py を起動します")
    led.on()
    proc = subprocess.Popen(["python3", ASSISTANT_SCRIPT, host])
    state["assistant_process"] = proc
    
    # モニタースレッド開始
    threading.Thread(target=monitor_assistant, daemon=True).start()

def stop_assistant():
    proc = state.get("assistant_process")
    if proc and proc.poll() is None:
        print("assistant.py を終了させます...")
        proc.terminate()
        try:
            proc.wait(timeout=5)
            print("assistant.py 終了成功")
        except subprocess.TimeoutExpired:
            print("assistant.py 応答なし → 強制終了")
            proc.kill()
    state["assistant_process"] = None

# ==== Raspberry Pi のシャットダウン ====
def stop_pi():
    print("🛑 Raspberry Pi をシャットダウンします...")
    
    # GPIOを明示的に解放
    cleanup_gpio()
    
    # テスト用：本番運用時にコメントアウト解除
    os.system("sudo shutdown -h now")

# ==== シャットダウン処理 ====
def handle_shutdown():
    print("⚫ シャットダウン処理中...")
    state["mode"] = Mode.SHUTTING_DOWN
    stop_assistant()
    if state["host"]:
        stop_voicevox(state["host"])
        stop_ec2()
        state["host"] = None
    led.off()
    state["mode"] = Mode.IDLE
    print("✅ シャットダウン完了。Raspberry Pi をシャットダウンします。")
    
    #テスト用にコメントアウト
    stop_pi()

# ==== ボタンイベント ====
def on_button_pressed():
    mode = state["mode"]
    print(f"🔘 ボタンが押されました（現在のモード: {mode}）")

    if mode == Mode.IDLE:
        state["mode"] = Mode.STARTING
        
        host = start_ec2()
        if host and start_voicevox(host):
            state["host"] = host
            state["mode"] = Mode.TALKING
            start_assistant(host)
        else:
            print("初期化失敗。IDLEに戻ります。")
            state["mode"] = Mode.IDLE
            led.off()

    elif mode == Mode.TALKING:
        handle_shutdown()

    else:
        print("⚠ 処理中です。ボタン操作は無効です。")

# ==== 起動時処理 ====
try:
    button.when_pressed = on_button_pressed
    print("✨ ボタン操作を待っています...")
    pause()
finally:
    cleanup_gpio()

