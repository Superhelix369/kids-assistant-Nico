import asyncio
import threading
import time
from bleak import BleakClient
from config import PICO_MAC, WRITE_UUID

_loop = None
_thread = None
_ready = threading.Event()

_cmd_queue = None          # asyncio.Queue (loop内で作る)
_client = None
_connected = False

def _loop_thread():
    global _loop, _cmd_queue
    _loop = asyncio.new_event_loop()
    asyncio.set_event_loop(_loop)
    _cmd_queue = asyncio.Queue()
    _ready.set()
    _loop.create_task(_ble_worker())
    _loop.run_forever()

def _ensure_loop():
    global _thread
    if _thread is None or not _thread.is_alive():
        _ready.clear()
        _thread = threading.Thread(target=_loop_thread, daemon=True)
        _thread.start()
    _ready.wait(timeout=3)
    if _loop is None:
        raise RuntimeError("BLE loop failed to start")

async def _connect():
    global _client, _connected
    if _client is not None:
        try:
            await _client.disconnect()
        except:
            pass
        _client = None
        _connected = False

    _client = BleakClient(PICO_MAC)

    # connect は失敗しうるのでリトライ前提
    print(f"➡ Connecting to {PICO_MAC}")
    await _client.connect()
    _connected = True

async def _ensure_connected():
    global _connected
    if _connected and _client is not None and _client.is_connected:
        return
    await _connect()

async def _ble_worker():
    """
    送信はこの1本のタスクだけが担当する。
    connect/write の競合を完全に防ぐ。
    """
    backoff = 0.3
    while True:
        cmd = await _cmd_queue.get()
        try:
            # 接続できるまでリトライ（InProgressにならない）
            while True:
                try:
                    await _ensure_connected()
                    break
                except Exception as e:
                    print(f"⚠ BLE接続失敗: {e} / retry in {backoff:.1f}s")
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 1.5, 3.0)

            backoff = 0.3  # 接続できたら戻す

            # 送信
            await _client.write_gatt_char(WRITE_UUID, cmd.encode())
            print("📤 送信:", cmd)

        except Exception as e:
            # 送信失敗→接続を捨てて次で再接続
            print(f"⚠ BLE送信失敗: {cmd} / {e}")
            try:
                await _client.disconnect()
            except:
                pass
            globals()["_client"] = None
            globals()["_connected"] = False

        finally:
            _cmd_queue.task_done()

def send_cmd(cmd: str):
    """
    既存互換：同期関数のまま呼べる。
    ただし「キューに積むだけ」なので速い＆安全。
    """
    _ensure_loop()
    _loop.call_soon_threadsafe(_cmd_queue.put_nowait, cmd)
