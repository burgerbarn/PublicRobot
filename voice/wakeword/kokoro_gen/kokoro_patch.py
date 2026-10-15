"""Fix a per-call leak in kokoro-onnx 0.4.7 + phonemizer-fork 3.3.1 (found 2026-09-28 on bigbuddy):
kokoro_onnx.tokenizer.Tokenizer.phonemize() calls phonemizer.phonemize(), which builds a NEW EspeakBackend
every call; each one copies libespeak-ng.so into a fresh tempdir (never removed) and dlopens it again
(never unloaded): ~8 MB RAM + 648 KB /tmp per call. Patch: one EspeakBackend per language, reused.
Output is identical to phonemizer.phonemize(text, lang, preserve_punctuation=True, with_stress=True).
Use: import kokoro_patch; kokoro_patch.apply()  (before synthesizing)."""
from kokoro_onnx import tokenizer as _tok

_backends = {}


def _backend(lang):
    b = _backends.get(lang)
    if b is None:
        from phonemizer.backend import EspeakBackend
        b = _backends[lang] = EspeakBackend(lang, preserve_punctuation=True, with_stress=True)
    return b


def _phonemize(self, text, lang='en-us', norm=True):
    if norm:
        text = _tok.Tokenizer.normalize_text(text)
    phonemes = _backend(lang).phonemize([text], strip=False)[0]
    phonemes = ''.join(filter(lambda p: p in self.vocab, phonemes))
    return phonemes.strip()


def apply():
    if _tok.Tokenizer.phonemize is not _phonemize:
        _tok.Tokenizer._orig_phonemize = _tok.Tokenizer.phonemize
        _tok.Tokenizer.phonemize = _phonemize
