# 🚀 Kids_assistant-Nico（開発中）

![Python](https://img.shields.io/badge/Python-3.11-blue?logo=python&logoColor=white)
![Raspberry Pi](https://img.shields.io/badge/RaspberryPi-yes-green?logo=raspberry-pi)
![AWS](https://img.shields.io/badge/AWS-EC2-orange?logo=amazon-aws)
![OpenAI](https://img.shields.io/badge/OpenAI-API-purple?logo=openai&logoColor=white)
![VOICEVOX](https://img.shields.io/badge/VOICEVOX-v1-pink)


<!--![プロジェクト画像](./images/raspi.JPG)-->

---

## ① ✨ はじめに
ニコちゃんは、Raspberry PiとAIアシスタントを組み合わせた**子ども向け対話型システム**です。  

---

## ② 💡 きっかけ
- シンプルに未経験なので、開発がやってみたい
- Raspberry PiやAWSを活用した実践的な経験を積みたかった
- IT系の学校に通えることになったが、授業についていかれないと思い事前勉強
- 自分の子供が弟か妹を欲しがっている・・・
- ちょうど壊れてた歩くぬいぐるみ型のおもちゃがあったのでそれを使うことにする
  ※まだ、本体には実装していません。今後順次機能追加や本体への実装を行う予定です。

---

## ③ 🌐 詳細 / Webページ
<!--Notionで作成したプロジェクトページのリンクを貼ります。  -->
<!--[詳細はこちら](https://www.notion.so/your-page-url)-->

---

## ④ 🖼 図解 / フロー
<!--システム構成やフローを図で説明すると一目でわかります。  -->

<!--![システムフロー](./images/flow.png)-->

---

## ⑤ 🚀 特徴
- PCを使えない子供用にRaspberry Pi単体で自動起動・シャットダウン
- 子供が直感的に使用できるようにボタンを押したら会話ができるようになる
  （電源を入れると『ボタンを押してね』のアナウンスが流れる）
- voicevoxを使用して、幼児っぽい声
- 幼児っぽく言葉がうまくしゃべれないよう設定（OPEN AIのASSISTANT APIのプロンプトで指示）
- AWSの課金が心配すぎて、boto3でEC2の起動・停止をラズパイから制御できる
- 子供つけっぱなしによるAWSへのコスト増大が恐怖過ぎて、会話なしで一定時間経過すると自動シャットダウン

---

## ⑥ ⚙️ 今後の課題、やってみたいこと
- ASSISTANT APIが2026年に廃止になるため、RESPONSES APIへの早期移行
- SQLが苦手なので、勉強がてらAWSのRDSなどを使って、成長する＆会話の記憶
- カメラを追加して、顔を覚えてもらう


