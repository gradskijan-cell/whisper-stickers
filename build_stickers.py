"""Стикеры для «Шёпота»: скачивает паки Telegram по ссылкам из packs.txt и картинки из папки gifs/,
переделывает их в лёгкие WebP (анимированные остаются анимированными) и собирает список stickers.json.
Запускается сам в GitHub Actions (.github/workflows/stickers.yml). Токен бота берётся из секрета
TELEGRAM_BOT_TOKEN и никуда, кроме api.telegram.org, не уходит."""
import gzip, io, json, os, re, shutil, subprocess, sys, tempfile, time, urllib.error, urllib.parse, urllib.request

TOKEN = os.environ.get('TG_TOKEN', '').strip()
API = os.environ.get('TG_API', 'https://api.telegram.org').rstrip('/')
REBUILD = os.environ.get('REBUILD', '').strip().lower() in ('1', 'true', 'yes')
SIZE, FPS, MAX_SEC = 256, 20, 6
OUT, INDEX, PACKS, GIFS = 'stickers', 'stickers.json', 'packs.txt', 'gifs'
MEDIA = ('.gif', '.mp4', '.webm', '.mov', '.webp', '.png', '.jpg', '.jpeg', '.tgs')
problems = []

def say(*a): print(*a, flush=True)

def pack_names(text):
    out = []
    words = []
    for row in str(text or '').splitlines():
        words += re.split(r'[\s,]+', row.split('#', 1)[0].strip())
    for line in words:
        if not line: continue
        m = re.search(r'(?:t\.me|telegram\.me|telegram\.dog)/(?:addstickers|addemoji)/([A-Za-z0-9_]+)', line)
        name = m.group(1) if m else (line if re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{2,63}', line) else '')
        if name and name not in out: out.append(name)
    return out

def tg(method, **params):
    url = '%s/bot%s/%s?%s' % (API, TOKEN, method, urllib.parse.urlencode(params))
    for attempt in range(4):
        try:
            with urllib.request.urlopen(url, timeout=60) as r: j = json.load(r)
        except urllib.error.HTTPError as e:
            try: j = json.load(e)
            except Exception: j = {'ok': False, 'description': 'HTTP %s' % e.code}
            if e.code == 429 and attempt < 3:
                time.sleep(int((j.get('parameters') or {}).get('retry_after', 5)) + 1); continue
        except Exception as e:
            if attempt < 3: time.sleep(3); continue
            raise RuntimeError('нет связи с Telegram: %s' % e)
        if not j.get('ok'): raise RuntimeError(j.get('description') or 'ошибка Telegram')
        return j['result']

def tg_file(file_id):
    path = tg('getFile', file_id=file_id)['file_path']
    with urllib.request.urlopen('%s/file/bot%s/%s' % (API, TOKEN, path), timeout=120) as r: return path, r.read()

def fit(im):
    im = im.convert('RGBA'); im.thumbnail((SIZE, SIZE)); return im

def save_frames(frames, dst, ms):
    if len(frames) == 1: frames[0].save(dst, 'WEBP', quality=85, method=6); return False
    frames[0].save(dst, 'WEBP', save_all=True, append_images=frames[1:], duration=ms, loop=0, quality=72, method=4)
    return True

def conv_image(src, dst):
    from PIL import Image, ImageSequence
    im = Image.open(src)
    if getattr(im, 'is_animated', False):
        frames, ms = [], max(20, int(im.info.get('duration') or 50))
        for i, fr in enumerate(ImageSequence.Iterator(im)):
            if i * ms > MAX_SEC * 1000: break
            frames.append(fit(fr.copy()))
        return save_frames(frames, dst, ms)
    return save_frames([fit(im)], dst, 0)

def conv_tgs(src, dst):
    from rlottie_python import LottieAnimation
    lib = os.environ.get('RLOTTIE_LIB') or None
    anim = LottieAnimation.from_tgs(src, rlottie_lib_path=lib) if lib else LottieAnimation.from_tgs(src)
    with anim:
        total = max(1, anim.lottie_animation_get_totalframe()); fr = max(1, anim.lottie_animation_get_framerate())
        w, h = anim.lottie_animation_get_size(); sc = SIZE / max(w, h, 1); w, h = max(1, round(w * sc)), max(1, round(h * sc))
        n = max(1, min(int(total / fr * FPS), MAX_SEC * FPS)); frames = []
        for i in range(n):
            frames.append(anim.render_pillow_frame(frame_num=min(total - 1, int(i * fr / FPS)), width=w, height=h).copy())
    return save_frames(frames, dst, int(1000 / FPS))

def conv_video(src, dst):
    from PIL import Image
    tmp = tempfile.mkdtemp()
    try:
        vf = 'fps=%d,scale=%d:%d:force_original_aspect_ratio=decrease:flags=lanczos,format=rgba' % (FPS, SIZE, SIZE)
        base = ['ffmpeg', '-v', 'error', '-y']
        tries = ([base + ['-c:v', 'libvpx-vp9', '-i', src]] if src.lower().endswith('.webm') else []) + [base + ['-i', src]]
        ok = False
        for cmd in tries:
            shutil.rmtree(tmp, ignore_errors=True); os.makedirs(tmp)
            r = subprocess.run(cmd + ['-t', str(MAX_SEC), '-vf', vf, '-an', os.path.join(tmp, 'f%04d.png')], capture_output=True, text=True)
            if r.returncode == 0 and os.listdir(tmp): ok = True; break
        if not ok: raise RuntimeError('ffmpeg не смог открыть файл')
        frames = [fit(Image.open(os.path.join(tmp, f))) for f in sorted(os.listdir(tmp))]
        return save_frames(frames, dst, int(1000 / FPS))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

def convert(src, dst):
    ext = os.path.splitext(src)[1].lower()
    if ext == '.tgs': return conv_tgs(src, dst)
    if ext in ('.webm', '.mp4', '.mov'): return conv_video(src, dst)
    return conv_image(src, dst)

TR = dict(zip('абвгдеёжзийклмнопрстуфхцчшщъыьэюяіїєґ', 'a b v g d e e zh z i y k l m n o p r s t u f h ts ch sh sch _ y _ e yu ya i yi ye g'.split()))
def slug(s, n=20):
    s = ''.join(TR.get(c, c) for c in str(s).lower()).replace('_', '')
    s = re.sub(r'[^a-z0-9]+', '', s); return s[:n] or 'p'

def load_index():
    try:
        with open(INDEX, encoding='utf-8') as f: j = json.load(f)
        return j if isinstance(j, dict) else {}
    except Exception: return {}

def build_pack(name, used, old_desc):
    st = tg('getStickerSet', name=name)
    sl = slug(name)
    while sl in used: sl = sl[:18] + str(len(used))
    used.add(sl); folder = os.path.join(OUT, sl)
    shutil.rmtree(folder, ignore_errors=True); os.makedirs(folder, exist_ok=True)
    items, tmp = [], tempfile.mkdtemp()
    try:
        for i, s in enumerate(st.get('stickers') or [], 1):
            sid = '%s_%03d' % (sl, i)
            try:
                path, data = tg_file(s['file_id'])
                ext = '.tgs' if s.get('is_animated') else ('.webm' if s.get('is_video') else (os.path.splitext(path)[1] or '.webp'))
                src = os.path.join(tmp, '%03d%s' % (i, ext))
                with open(src, 'wb') as f: f.write(data)
                rel = '%s/%03d.webp' % (folder, i); anim = convert(src, rel)
                emo = s.get('emoji') or ''
                items.append({'id': sid, 'file': rel.replace(os.sep, '/'), 'emoji': emo, 'desc': old_desc.get(sid) or emo, 'pack': sl, 'anim': 1 if anim else 0})
            except Exception as e:
                problems.append('%s, стикер %d: %s' % (name, i, e)); say('  ⚠', name, i, e)
            if i % 20 == 0: say('  …', i)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return {'name': name, 'title': st.get('title') or name, 'slug': sl, 'count': len(items), 'link': 'https://t.me/addstickers/' + name}, items

def build_gifs(old, old_desc):
    if not os.path.isdir(GIFS): return None, []
    files = sorted(f for f in os.listdir(GIFS) if f.lower().endswith(MEDIA))
    if not files: return None, []
    folder = os.path.join(OUT, 'gifs'); os.makedirs(folder, exist_ok=True)
    have = {it['id']: it for it in old if it.get('pack') == 'gifs'}
    items, keep = [], set()
    for f in files:
        base = os.path.splitext(f)[0]; sid = 'gif_' + slug(base, 32); n = 2
        while sid in keep: sid = 'gif_%s%d' % (slug(base, 30), n); n += 1
        keep.add(sid); rel = '%s/%s.webp' % (folder, sid[4:]); src = os.path.join(GIFS, f)
        desc = old_desc.get(sid) or re.sub(r'[_\-.]+', ' ', base).strip()
        try:
            if REBUILD or sid not in have or not os.path.exists(rel) or os.path.getmtime(src) > os.path.getmtime(rel):
                anim = convert(src, rel)
            else: anim = bool(have[sid].get('anim'))
            items.append({'id': sid, 'file': rel.replace(os.sep, '/'), 'emoji': '', 'desc': desc, 'pack': 'gifs', 'anim': 1 if anim else 0})
        except Exception as e:
            problems.append('gifs/%s: %s' % (f, e)); say('  ⚠', f, e)
    for x in os.listdir(folder):
        if x.endswith('.webp') and 'gif_' + x[:-5] not in keep: os.remove(os.path.join(folder, x))
    return {'name': 'gifs', 'title': 'Гифки', 'slug': 'gifs', 'count': len(items), 'link': ''}, items

def main():
    text = open(PACKS, encoding='utf-8').read() if os.path.exists(PACKS) else ''
    extra = [n for n in pack_names(os.environ.get('PACKS_INPUT', '')) if n not in pack_names(text)]
    if extra:
        text = text.rstrip('\n') + ('\n' if text.strip() else '') + ''.join('https://t.me/addstickers/%s\n' % n for n in extra)
        with open(PACKS, 'w', encoding='utf-8') as f: f.write(text)
        say('Добавила в packs.txt:', ', '.join(extra))
    names = pack_names(text)
    old = load_index(); old_items = old.get('stickers') or []; old_desc = {it['id']: it.get('desc') for it in old_items if it.get('desc')}
    old_packs = {p['name']: p for p in (old.get('packs') or []) if p.get('name') != 'gifs'}
    os.makedirs(OUT, exist_ok=True)
    packs, items, used = [], [], set()
    for name in names:
        prev = old_packs.get(name)
        if prev and not REBUILD and os.path.isdir(os.path.join(OUT, prev['slug'])):
            used.add(prev['slug']); packs.append(prev); items += [it for it in old_items if it.get('pack') == prev['slug']]
            say('✓ уже есть:', name); continue
        if not TOKEN:
            problems.append('нет секрета TELEGRAM_BOT_TOKEN — пак %s не скачан' % name); continue
        say('↓ качаю пак:', name)
        try:
            p, its = build_pack(name, used, old_desc); packs.append(p); items += its
            say('  готово: %s — %d стикеров' % (p['title'], p['count']))
        except Exception as e:
            problems.append('пак %s: %s' % (name, e)); say('  ✗', name, e)
    gp, gi = build_gifs(old_items, old_desc)
    if gp: packs.append(gp); items += gi; say('✓ гифки:', gp['count'])
    keep = {p['slug'] for p in packs}
    for d in os.listdir(OUT):
        if os.path.isdir(os.path.join(OUT, d)) and d not in keep: shutil.rmtree(os.path.join(OUT, d)); say('🗑 убран пак:', d)
    with open(INDEX, 'w', encoding='utf-8') as f:
        json.dump({'v': 1, 'updated': time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime()), 'packs': packs, 'stickers': items}, f, ensure_ascii=False, indent=1)
    say('\nИтого: %d паков, %d стикеров.' % (len(packs), len(items)))
    if problems:
        say('\nНе получилось:'); [say(' -', p) for p in problems[:40]]
        if not items or any('TELEGRAM_BOT_TOKEN' in p or p.startswith('пак ') for p in problems): sys.exit(1)

if __name__ == '__main__':
    main()
