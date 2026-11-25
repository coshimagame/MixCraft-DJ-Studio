# MIDI mapping

MIDI入出力は未実装。midi_map.pyに暫定マッピング/Learn/LEDロジックのみ存在。
APC40初代、APC40 MKII、DDJ-RBすべて実機未確認。
推測したnote/CC番号は出荷プロファイルにしない。
次段階でUSB識別、ポート選択、Learn、channel/note/CC、絶対/相対値、LED feedbackを実装。
実機がない場合は合成MIDIのnote-on/off、CC境界、連続フェーダー、切断・再接続をテストする。
プロファイルにはmodel、firmware、検証日、実機/シミュレーションの別を記録する。
