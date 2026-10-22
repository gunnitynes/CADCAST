"""Build the GitHub Pages versions of CADCAST (docs/go and docs/patch) from cloud/*.html.

  python3 cloud/build.py

The templates reuse cadcast.py's own CSS, shared JS and level-meter worklet, so the cloud pages look and sound
like the local ones.
Except the patch bay: cloud/patch.html has its own stylesheet in Control Room's Bauhaus look (it follows
Control Room's theme, controlroom.theme) and its own faders; it no longer uses __CSS__ / __PATCH_CSS__. Run this after changing either, then commit docs/.
"""
import importlib.util, os

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
spec = importlib.util.spec_from_file_location('cadcast', os.path.join(ROOT, 'cadcast.py'))
cc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cc)   # safe: the server only starts under __main__

for name, extra in (('go', cc.PLUG_CSS), ('patch', cc.PATCH_CSS)):
    html = open(os.path.join(HERE, name + '.html')).read()
    html = (html.replace('__CSS__', cc.CSS).replace('__PLUG_CSS__', extra).replace('__PATCH_CSS__', extra)
                .replace('__JS_COMMON__', cc.JS_COMMON).replace('__PEAK_JS__', cc.PEAK_JS).replace('__ICON__', cc.ICON))
    out = os.path.join(ROOT, 'docs', name, 'index.html')
    os.makedirs(os.path.dirname(out), exist_ok=True)
    open(out, 'w').write(html)
    print('wrote', os.path.relpath(out, ROOT), len(html), 'bytes')
