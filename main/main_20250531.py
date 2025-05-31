import config
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
        
def on_button_held():
    print("🧯 ボタンが長押しされました → 強制シャットダウン")
    handle_shutdown()
    
def is_dev_mode():
    return DEV_MODE
        
def main():
    print("🟢 main.py 開始！")
    play_button_prompt()
    button.when_pressed = on_button_pressed
    button.when_held = on_button_held
    button.hold_time = 3

    try:
        while True:
            time.sleep(config.SPEAKER_ID)
    except KeyboardInterrupt:
        print("🛑 Ctrl+C が検出されました → シャットダウンします")
        handle_shutdown()

atexit.register(cleanup_gpio)

if __name__ == "__main__":
    main()