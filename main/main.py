
import boto3
import time
import subprocess
import lgpio
from gpiozero import Button, LED, Device
from gpiozero.pins.lgpio import LGPIOFactory
import socket
import requests
import os
import threading
import traceback
import atexit
import config
from enum import Enum
from datetime import datetime
  


class SafeButton(Button):
    """
    gpiozero の race 条件による AttributeError を握りつぶしつつ、
    本来のイベントをそのまま流す安全ラッパー。
    """
    def _fire_activated(self):
        try:
            super()._fire_activated()
        except AttributeError as e:
            # _hold_thread が None のままアクセスされるレアケース
            if "holding" in str(e):
                print("⚠ SafeButton: race-condition AttributeError を無視しました")
            else:
                raise

# ======= ユーザー設定 =======
INSTANCE_ID = config.INSTANCE_ID
REGION = config.AWS_REGION
VOICEVOX_PORT = config.VOICEVOX_PORT
EC2_HOST = config.EC2_HOST
ASSISTANT_SCRIPT = config.ASSISTANT_SCRIPT
SSH_KEY_PATH = config.SSH_KEY_PATH
venv_python = config.VENV_PYTHON
DEV_MODE = config.DEV_MODE
# ===========================

# ハードウェア設定
Device.pin_factory = LGPIOFactory()
button = SafeButton(17, pull_up=True, bounce_time=0.3) 
led = LED(18)

# 🛡️ シャットダウン処理の多重実行防止フラグとロック
shutdown_lock = threading.Lock()
shutdown_initiated = False
gpio_cleaned_up = False  # ✅ 追加：多重 cleanup を防止

# ===== GPIO解放 =====
def cleanup_gpio():
    global gpio_cleaned_up
    if gpio_cleaned_up:
        print("🛑 cleanup_gpio() は既に実行済みです")
        return
    
    print("🪝 GPIO解放中...")
    
    # -------- ボタン ----------
    try:
        if button is not None:
            button.when_pressed = None 
            button.when_held = None
            button.when_released = None
            
            # ✅ 安全にスレッド停止を試みる（内部アクセスだけど有効）
            if hasattr(button, '_hold_thread') and button._hold_thread is not None:
                try:
                    print("🛑 hold_thread を停止中...")
                    button._hold_thread.stop()
                     # stop() が戻ればスレッドは終了に向かうので、
                     # オブジェクト自体は残しておいた方が安全
                    button._hold_thread.join(timeout=1)  # 念のため待つ
                except Exception as e:
                    print("⚠ hold_thread 停止エラー:", e)
            
            time.sleep(0.5)  # 少しだけ待つことでスレッド内の処理が終わる
            button.close()
            time.sleep(0.5)
            print("🔓 gpiozero による GPIO 解放 OK")           
    except Exception as e:
        print("⚠ button.close() エラー:", e)

    # -------- LED ----------
    try:
        if led is not None:
            led.close()
            print("🔓 gpiozero による LED 解放OK")
            time.sleep(0.3)
    except Exception as e:
        print("⚠ led.close() エラー:", e)
        
    # gpiozero が失敗した場合のみ、強制解放実行  
    try:
        handle = lgpio.gpiochip_open(0)
        lgpio.gpiochip_close(handle)
        print("🔓 lgpio による GPIO 解放OK")
    except Exception as e:
        print("⚠ lgpio.gpiochip_open/close() エラー:", e)
    
    # -------- 残プロセス・サービス ----------    
    try:
        my_pid = str(os.getpid())
        pids_output = subprocess.run(["sudo", "lsof", "-t", "/dev/gpiochip0"], capture_output=True, text=True)
        pids = [pid for pid in pids_output.stdout.strip().splitlines() if pid != my_pid]
    
        for pid in pids:
            print(f"⚠ GPIO 使用中のプロセスを強制終了: PID={pid}")
            subprocess.run(["sudo", "kill", "-9", pid], check=False)

        # ✅ systemctl stop lgpio の戻り値とstderrを確認
        try:
            result = subprocess.run(["sudo", "systemctl", "stop", "lgpio"], capture_output=True, text=True)
            if result.returncode != 0:
                print(f"⚠ lgpio.service 停止失敗: {result.stderr.strip()}")
            else:
                print("🛑 lgpio.service 停止成功")
        except Exception as e:
            print(f"⚠ systemctl stop lgpio 実行中に例外発生: {e}")

         # ✅ killall -9 lgpiod の戻り値と stderr を確認（no process found を例外扱いしない）
        try:
            result = subprocess.run(["sudo", "killall", "-9", "lgpiod"], capture_output=True, text=True)
            if result.returncode != 0:
                if "no process found" in result.stderr:
                    print("ℹ lgpiod プロセスは実行されていませんでした（問題なし）")
                else:
                    print(f"⚠ lgpiod killall 失敗: {result.stderr.strip()}")
            
            else:
                print("🛑 lgpiod killall 成功")
        except Exception as e:
            print(f"⚠ lgpiod killall 実行中に例外発生: {e}")

        print("✅ GPIO cleanup 強制モード完了")

    except Exception as e:
        print(f"⚠ 強制GPIO解放エラー: {e}")

    gpio_cleaned_up = True

# ===== 状態管理 =====
class Mode(Enum):
    IDLE = 1
    STARTING = 2
    TALKING = 3
    SHUTTING_DOWN = 4

state = {
    "mode": Mode.IDLE,
    "host": EC2_HOST,
    "assistant_process": None,
}

ec2 = boto3.client('ec2', region_name=REGION)

# ===== 接続・待機処理 =====
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

def wait_for_voicevox(host, port=VOICEVOX_PORT, timeout=60):
    print(f"🔄 VoiceVox 起動確認中: http://{host}:{port}")
    start_time = time.time()
    while time.time() - start_time < timeout:
        try:
            with socket.create_connection((host, port), timeout=3):
                response = requests.get(f"http://{host}:{port}", timeout=3)
                if response.status_code == 200:
                    speakers_response = requests.get(f"http://{host}:{port}/speakers", timeout=3)
                    if speakers_response.status_code == 200:
                        print("✅ VoiceVox 完全起動確認！")
                        return True
        except Exception as e:
            print(f"⏳ ポート接続待ち中… {e}")
        time.sleep(2)
    print("❌ VoiceVox 起動タイムアウト")
    return False

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
        proc.wait(timeout=900)
        print("🛑 assistant.py が正常終了しました（またはタイムアウト）")
    except subprocess.TimeoutExpired:
        print("⏰ assistant.py がタイムアウトしました → 強制シャットダウンします")
        proc.kill()
        proc.wait()
    finally:
        try:
            handle_shutdown()
        except Exception as e:
            print("⚠ handle_shutdown 中に例外:", e)

def start_assistant(host):
    print("🧠 assistant.py を起動します")
    led.on()
    try:
        proc = subprocess.Popen([venv_python, ASSISTANT_SCRIPT, host])
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
        
        try:
            proc.terminate()
            proc.wait(timeout=5)
            print("✅ assistant.py 終了成功")
        except subprocess.TimeoutExpired:
            print("⚠ assistant.py 応答なし → 強制終了")
            proc.kill()
            proc.wait()
        except Exception as e:
            print("⚠ assistant.py 終了処理中にエラー:", e)
    state["assistant_process"] = None

def stop_pi():
    print("🛑 Raspberry Pi をシャットダウンします...")
    if DEV_MODE:
        print("🧪 DEV_MODE: シャットダウンはスキップされました")
        return
    try:
        subprocess.run(["sudo", "shutdown", "-h", "now"], check=True)
    except Exception as e:
        print(f"⚠ シャットダウン失敗: {e}")


def handle_shutdown():
    global shutdown_initiated
    with shutdown_lock:
        if shutdown_initiated:
            print("⚠️ シャットダウン処理はすでに実行されています。スキップします。")
            return
        shutdown_initiated = True
        
    print("⚫ シャットダウン処理中...")
    state["mode"] = Mode.SHUTTING_DOWN

    try:
        try:
            with open(config.SHUTDOWN_LOG_PATH, "a") as f:
                f.write(f"[{datetime.now()}] シャットダウン開始\n")
        except Exception as e:
            print(f"⚠️ ログ書き込み失敗（開始）: {e}")

        stop_assistant()

        if state["host"]:
            stop_ec2()
            state["host"] = None

        try:
            led.off()
        except Exception as e:
            print(f"⚠️ LED.off() でエラー: {e}")

        try:
            with open(config.SHUTDOWN_LOG_PATH, "a") as f:
                f.write(f"[{datetime.now()}] stop_pi() 呼び出し前\n\n")
        except Exception as e:
            print(f"⚠️ ログ書き込み失敗（終了前）: {e}")

        print("✅ シャットダウン完了。Raspberry Pi をシャットダウンします。")

    except Exception as e:
        print(f"❌ handle_shutdown() 内で予期せぬ例外: {e}")

    finally:
        try:
            cleanup_gpio()
        except Exception as e:
            print(f"⚠️ GPIO解放中に例外が発生: {e}")

        if not DEV_MODE:
            threading.Thread(target=stop_pi).start()


def play_button_prompt():
    print("🔈『ボタンを押してね』の音声を再生します...")
    try:
        subprocess.run(["aplay",  config.BUTTON_AUDIO_PATH], check=True)
        print("✅ 音声再生完了")
    except Exception as e:
        print("⚠ 音声再生エラー:", e)

def on_button_pressed():
    try:
        if not button.is_pressed:
            print("⚠ ボタンイベントが来たが、実際には押されていません → 無視します")
            return

        mode = state.get("mode", Mode.IDLE)
        print(f"🔘 ボタンが押されました（現在のモード: {mode}）")

        if mode == Mode.IDLE:
            state["mode"] = Mode.STARTING
            host = start_ec2()
            if host and wait_for_voicevox(host):
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
    except Exception as e:
        print("❗ on_button_pressed 内でエラー:", e)
        traceback.print_exc()
        # 安全のためシャットダウン処理を呼び出す
        handle_shutdown()
    
    
def is_dev_mode():
    return DEV_MODE
        
def main():
    print("🟢 main.py 開始！")
    print("🛠 GPIO 初期化開始")
    # 初期化はすでにグローバルで済んでいるためログだけ
    print("✅ GPIO 初期化成功")

    play_button_prompt()
    button.when_pressed = on_button_pressed

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("🛑 Ctrl+C が検出されました → シャットダウンします")
        handle_shutdown()



atexit.register(cleanup_gpio)

if __name__ == "__main__":
    main()

