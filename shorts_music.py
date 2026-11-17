"""
shorts_music.py  -  original background music for Thrillcheck Shorts, composed in code

Every track is synthesized from scratch (drums, bass, chords, melody), so there are no music rights
to clear and no copyright claims on YouTube, Instagram or TikTok.

Styles:
    hype      128 BPM four-on-the-floor sports anthem: punchy kick, claps, hats, sub bass, bright plucks
    gameshow  112 BPM bouncy quiz-show groove: staccato bass, ticking hats, playful mallet melody
    lazy      78 BPM lo-fi: soft kick, warm electric-piano chords, vinyl crackle (for the Snoozefest)

Each video gets its own key, chord progression and melody (picked from the video's name), so a week of
uploads doesn't sound like the same loop.
"""
import hashlib
import math

import numpy as np
from scipy.signal import butter, lfilter

SR = 44100

KEYS = {"A": 57, "B": 59, "C": 60, "D": 62, "E": 64, "F": 65, "G": 55}       # MIDI root notes
MINOR_PROGS = [[0, 8, 3, 10], [0, 5, 8, 7], [0, 10, 8, 7], [0, 3, 8, 10]]       # i-VI-III-VII, i-iv-VI-V, ...
MAJOR_PROGS = [[0, 7, 9, 5], [0, 5, 9, 7], [0, 9, 5, 7], [5, 7, 0, 9]]          # I-V-vi-IV, ...


def hz(m):
    return 440.0 * 2 ** ((m - 69) / 12)


def _filt(x, kind, cutoff, order=2):
    nyq = SR / 2
    if kind == "band":
        b, a = butter(order, [cutoff[0] / nyq, cutoff[1] / nyq], btype="band")
    else:
        b, a = butter(order, cutoff / nyq, btype=kind)
    return lfilter(b, a, x)


def _env(n, attack, decay):
    t = np.arange(n) / SR
    return np.clip(t / max(attack, 1e-4), 0, 1) * np.exp(-decay * t)


def _add(buf, sig, at):
    i0 = int(at * SR)
    if i0 >= len(buf) or i0 < 0:
        return
    i1 = min(len(buf), i0 + len(sig))
    buf[i0:i1] += sig[: i1 - i0]


# ------------------------------------------------------------------ instruments
def kick(soft=False):
    n = int(SR * 0.32)
    t = np.arange(n) / SR
    f = 45 + 110 * np.exp(-t * 32)
    body = np.sin(2 * math.pi * np.cumsum(f) / SR) * np.exp(-t * (9 if soft else 7))
    click = np.random.default_rng(1).standard_normal(n) * np.exp(-t * 300) * (0.05 if soft else 0.18)
    return (body + click) * (0.7 if soft else 1.0)


def clap():
    n = int(SR * 0.22)
    t = np.arange(n) / SR
    noise = _filt(np.random.default_rng(2).standard_normal(n), "band", (900, 5000))
    env = np.exp(-t * 18) * (1 + 0.6 * ((t % 0.011) < 0.004) * (t < 0.035))
    return 0.55 * noise * env


def hat(open_=False, seed=3):
    n = int(SR * (0.18 if open_ else 0.045))
    t = np.arange(n) / SR
    noise = _filt(np.random.default_rng(seed).standard_normal(n), "high", 7000)
    return (0.22 if open_ else 0.16) * noise * np.exp(-t * (18 if open_ else 90))


def bass(m, dur, staccato=False):
    n = int(SR * dur)
    t = np.arange(n) / SR
    f = hz(m)
    wave_ = np.sin(2 * math.pi * f * t) + 0.35 * np.sin(2 * math.pi * 2 * f * t) + 0.12 * np.sin(2 * math.pi * 3 * f * t)
    env = _env(n, 0.004, 9 if staccato else 2.2)
    if not staccato:
        env *= np.clip((dur - t) / 0.03, 0, 1)
    return 0.45 * wave_ * env


def pluck(m, dur=0.35, bright=1.0):
    n = int(SR * dur)
    t = np.arange(n) / SR
    f = hz(m)
    s = sum(np.sin(2 * math.pi * f * k * t) * (bright ** (k - 1)) / k * np.exp(-t * (6 + 5 * k)) for k in range(1, 6))
    return 0.22 * s * _env(n, 0.002, 1.5)


def mallet(m, dur=0.3):
    n = int(SR * dur)
    t = np.arange(n) / SR
    f = hz(m)
    s = np.sin(2 * math.pi * f * t) + 0.25 * np.sin(2 * math.pi * 4.0 * f * t) * np.exp(-t * 30)
    return 0.24 * s * _env(n, 0.002, 9)


def pad(notes, dur, warm=False):
    n = int(SR * dur)
    t = np.arange(n) / SR
    s = np.zeros(n)
    for m in notes:
        for det in (-0.12, 0.0, 0.12):
            f = hz(m) * 2 ** (det / 12)
            s += np.sin(2 * math.pi * f * t + det) + (0.0 if warm else 0.18 * np.sin(2 * math.pi * 2 * f * t))
    env = np.clip(t / (0.25 if warm else 0.12), 0, 1) * np.clip((dur - t) / 0.2, 0, 1)
    if warm:                                    # electric-piano tremolo
        env *= 0.8 + 0.2 * np.sin(2 * math.pi * 4.5 * t)
    return 0.05 * s * env


def riser(dur):
    n = int(SR * dur)
    t = np.arange(n) / SR
    noise = np.random.default_rng(9).standard_normal(n)
    sweep = np.zeros(n)
    seg = max(1, n // 24)
    for i in range(0, n, seg):                  # step a band-pass upward
        lo = 300 + 6000 * (i / n) ** 2
        sweep[i:i + seg] = _filt(noise[i:i + seg], "band", (lo, min(lo * 2.2, 18000)))[: len(sweep[i:i + seg])]
    return 0.10 * sweep * (t / dur) ** 2


def crackle(n, seed):
    rng = np.random.default_rng(seed)
    x = np.zeros(n)
    idx = rng.integers(0, n, size=n // 2200)
    x[idx] = rng.uniform(-1, 1, size=len(idx))
    return 0.05 * _filt(x, "high", 1500) + 0.004 * _filt(rng.standard_normal(n), "band", (400, 3000))


# ------------------------------------------------------------------ arranging
def _plan(seed_text, style):
    h = int(hashlib.md5(seed_text.encode()).hexdigest(), 16)
    key = list(KEYS.values())[h % len(KEYS)]
    if style == "gameshow":
        prog, minor = MAJOR_PROGS[(h >> 8) % len(MAJOR_PROGS)], False
    elif style == "lazy":
        prog, minor = [[0, 5, 3, 8], [0, 8, 5, 7]][(h >> 8) % 2], True
    else:
        minor = (h >> 4) % 3 != 0               # mostly minor: sounds more dramatic
        prog = (MINOR_PROGS if minor else MAJOR_PROGS)[(h >> 8) % 4]
    return h, key, prog, minor


def _chord(root, deg, minor):
    """Triad built from the key's own scale (natural minor or major) on the given degree."""
    scale = [0, 2, 3, 5, 7, 8, 10] if minor else [0, 2, 4, 5, 7, 9, 11]
    i = scale.index(deg % 12) if deg % 12 in scale else 0
    notes = []
    for k in (0, 2, 4):
        j = i + k
        notes.append(root + scale[j % 7] + 12 * (j // 7))
    return notes


def compose(duration, style="hype", seed_text="thrillcheck"):
    """Return a mono float array (-1..1) of original music, `duration` seconds long."""
    h, key, prog, minor = _plan(seed_text, style)
    bpm = {"hype": 128, "gameshow": 112, "lazy": 78}[style] + (h >> 12) % 5 - 2
    beat = 60.0 / bpm
    bar = 4 * beat
    n = int(SR * (duration + 1))
    drums, low, mid = np.zeros(n), np.zeros(n), np.zeros(n)
    rng = np.random.default_rng(h % (2 ** 32))
    melody_shape = rng.choice([0, 2, 4, 7, 9, 12], size=16)
    nbars = int(math.ceil(duration / bar)) + 1
    K, KS, CL = kick(), kick(soft=True), clap()
    for b in range(nbars):
        t0 = b * bar
        if t0 > duration:
            break
        deg = prog[b % len(prog)]
        ch = _chord(key, deg, minor)
        intro = b == 0                          # first bar: no drums, builds up
        outro = t0 > duration - bar * 1.05
        if style == "lazy":
            mid_chord = pad([c - 12 for c in ch] + [ch[0] + 10 - 12], bar, warm=True)
            _add(mid, mid_chord, t0)
            for k in range(4):
                if not intro:
                    if k in (0, 2):
                        _add(drums, KS, t0 + k * beat + (0.02 if k == 2 else 0))
                    if k in (1, 3):
                        _add(drums, 0.5 * CL, t0 + k * beat)
                _add(drums, 0.6 * hat(seed=b * 4 + k), t0 + k * beat + beat / 2)
            _add(low, bass(ch[0] - 24, beat * 1.8), t0)
            _add(low, bass(ch[0] - 24 + 7, beat * 1.6), t0 + 2 * beat)
            continue
        _add(mid, pad(ch, bar), t0)
        for k in range(4):
            bt = t0 + k * beat
            if style == "hype":
                if not intro:
                    _add(drums, K, bt)
                    if k in (1, 3):
                        _add(drums, CL, bt)
                    _add(drums, hat(open_=True, seed=b * 8 + k), bt + beat / 2)
                for q in range(4 if not intro else 2):
                    _add(drums, hat(seed=b * 16 + k * 4 + q), bt + q * beat / (4 if not intro else 2))
                if not intro:
                    _add(low, bass(ch[0] - 24, beat * 0.45), bt + beat / 2)
                for q in range(2):              # arpeggiated plucks, melody shifts each bar
                    step = int(melody_shape[(b * 2 + k * 2 + q) % 16])
                    note = ch[(k + q) % 3] + (12 if step >= 7 else 0)
                    if not outro or k < 2:
                        _add(mid, pluck(note, 0.3, bright=0.7 if intro else 0.85), bt + q * beat / 2)
            else:                               # gameshow
                if not intro:
                    if k in (0, 2):
                        _add(drums, K, bt)
                    else:
                        _add(drums, 0.6 * CL, bt)
                _add(drums, 0.8 * hat(seed=b * 8 + k), bt)
                _add(drums, 0.5 * hat(seed=b * 8 + k + 99), bt + beat / 2)
                _add(low, bass(ch[0] - 24 + (0 if k % 2 == 0 else 7), beat * 0.4, staccato=True), bt)
                step = int(melody_shape[(b * 4 + k) % 16])
                _add(mid, mallet(key + 12 + step, 0.28), bt + (beat / 2 if k % 2 else 0))
        if style == "hype" and b == 0:
            _add(mid, riser(bar), t0)
    # sidechain "pump": duck everything except drums right after each beat (modern sports-anthem feel)
    if style in ("hype", "gameshow"):
        t = np.arange(n) / SR
        phase = (t % beat) / beat
        pump = 0.55 + 0.45 * np.clip(phase / 0.35, 0, 1)
        pump[: int(bar * SR)] = 1.0
        low *= pump
        mid *= pump
    mix = drums + low + mid
    if style == "lazy":
        mix = _filt(mix, "low", 5500) + crackle(n, h % 1000)
    mix = _filt(mix, "high", 30)
    mix = mix[: int(SR * duration)]
    fade = int(SR * 0.9)                        # gentle fade at the very end
    mix[-fade:] *= np.linspace(1, 0, fade) ** 1.5
    peak = np.abs(mix).max() + 1e-9
    return mix / peak * 0.9
