# Third-party licenses / ライセンス確認

2026-11-24確認。商用リリースの法的確認が完了したという意味ではありません。
アプリ固有コードのライセンスはMw-fActoryによる決定待ち（無断でMIT等を付与しない）。

|依存|用途|ライセンス/参照|
|---|---|---|
|Python|runtime|PSF license: https://docs.python.org/3/license.html|
|PySide6 Essentials / Shiboken / Qt|UI|LGPLv3/GPL/commercial: https://doc.qt.io/qtforpython-6/licenses.html|
|NumPy|mix buffer|BSD-3-Clause + bundled notices: https://numpy.org/doc/stable/license.html|
|SciPy|resampling/correlation|BSD-3-Clause + bundled notices: https://scipy.org/about/|
|sounddevice / PortAudio|audio output|MIT: https://python-sounddevice.readthedocs.io/ and https://www.portaudio.com/license.html|
|soundfile / libsndfile|decode|BSD-3-Clause / LGPL: https://python-soundfile.readthedocs.io/ and https://libsndfile.github.io/libsndfile/|
|CFFI / pycparser|native bindings|MIT/BSD, see bundled metadata|
|PyInstaller|packager|GPL with bootloader exception: https://pyinstaller.org/en/stable/license.html|

動的ライブラリを分離するonedir形式。LGPLライブラリの差し替え・ライセンス通知・
対応するソース入手方法などの義務を配布段階で満たす必要がある。
collect_licenses.pyはインストール済みパッケージのlicense/copying/noticeを収集する。
Qtやlibsndfileの各wheelに含まれる追加codec等の条項も確認対象。
商用販売前に完全なSBOM、対応ソース提供、ユーザーの再リンク/デバッグ権利、
Qtプラグイン一覧、署名・EULAを専門家と確認すること。

未使用候補: JUCE https://juce.com/juce-legal/ 、Rubber Band https://breakfastquay.com/rubberband/ 、
SoundTouch https://www.surina.net/soundtouch/ 、FFmpeg https://ffmpeg.org/legal.html 。
これらは今回のバイナリに組み込んでいない。採用前に版とビルド構成ごとに再確認する。
