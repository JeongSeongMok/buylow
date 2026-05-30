<div align="center">

# buylow

**[QuantConnect LEAN](https://github.com/QuantConnect/Lean) を基盤とした、韓国株式（KOSPI/KOSDAQ）の自動アルゴリズム取引。**

戦略を一度書けば、**バックテスト**と**ライブ**取引の両方で同じコードを実行できます。

[English](./README.md) · [한국어](./README.ko.md) · 日本語

</div>

---

> ⚠️ **ステータス: 開発初期段階。** 現在バックテスト連携は動作しますが、韓国市場データと
> Toss証券のライブ取引連携は進行中です。**まだ実取引には使用できません。**

## 概要

buylow は LEAN エンジンをプラットフォームとして利用します。常駐する Python オーケストレーターが
タスクごとに LEAN（.NET）プロセスを起動し、韓国/Toss アダプターが市場定義とライブ取引のために
LEAN へプラグインとして組み込まれます。戦略は純粋な Python ファイルなので、*バックテストした
コードがそのまま*ライブで取引されます。詳細な設計は [docs/ARCHITECTURE.md](./docs/ARCHITECTURE.md)
を参照してください。

## 機能

- 取引戦略（BNF 平均回帰、トレンドフォローなど）を LEAN エンジンで実行
- **同一**の戦略コードでバックテストとライブ取引
- **自分の API キーを使用**（Toss、AI）— リポジトリにはキーを一切保存しない
- _予定:_ 戦略の選択・スケジューリング、AI 自然言語による戦略生成、ダッシュボード

## 前提条件

- [.NET 10 SDK](https://dotnet.microsoft.com/download)
- [Python 3.11](https://www.python.org/) および [uv](https://github.com/astral-sh/uv)
- git

## インストール

```bash
git clone https://github.com/JeongSeongMok/buylow.git
cd buylow
# セットアップスクリプト: 予定
```

## 設定

Toss・AI の API キーは、**gitignore された**ローカル設定ファイルに自分で入力します。キーが
コミットされることはありません。_（正確な設定ファイルはライブ連携の実装とともに確定予定。）_

## 使い方

現在利用可能: エンジン連携をエンドツーエンドで検証する **LEAN バックテストのスモークテスト**。

```bash
# LEAN フォーマットの市場データフォルダを指定
export LEAN_DATA_DIR=/path/to/lean/Data
./scripts/run-backtest.sh
```

終了コード `0` なら連携は正常です。詳細は [docs/DEVELOPMENT.md](./docs/DEVELOPMENT.md) を参照。

## ロードマップ

- [x] LEAN 連携（バックテスト、C# + Python）
- [ ] KRX 市場定義（取引時間、KRW、手数料/取引税）
- [ ] 韓国の過去データ ETL（KRX → LEAN フォーマット）
- [ ] Toss証券 ライブ取引アダプター
- [ ] オーケストレーター: スケジューリング、永続化、ダッシュボード、通知

## ドキュメント

- [アーキテクチャ](./docs/ARCHITECTURE.md) — システム設計と根拠
- [開発](./docs/DEVELOPMENT.md) — セットアップ・ビルド・実行
- [エージェントガイド](./CLAUDE.md) — AI 支援開発の規約

## 免責事項

本ソフトウェアは教育目的で提供されます。自動取引には重大な金融リスクが伴い、**利用は自己責任**
です。作者はいかなる金銭的損失にも責任を負いません。利用にあたっては、証券会社の API 利用規約
および適用されるすべての法令を遵守してください。

## ライセンス

未定（TBD）。
