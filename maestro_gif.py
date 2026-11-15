"""Prompt-generated vector scenes rendered locally as smooth looping GIFs.

The model supplies declarative SVG layers and bounded motion, never executable code.
Dependencies: requirements-gif.txt, installed into the isolated .gif-deps directory.
JSON output protocol: https://api-docs.deepseek.com/guides/json_mode/
"""
from __future__ import annotations

import io
import json
import math
import pathlib
import re
import sys
import threading
import xml.etree.ElementTree as ET

# Keep optional renderer packages isolated from the NAS Python installation.
DEPS = pathlib.Path(__file__).with_name('.gif-deps')
if DEPS.is_dir() and str(DEPS) not in sys.path:
    sys.path.insert(0, str(DEPS))

RENDER_LOCK = threading.Lock()
TAGS = {'g', 'defs', 'linearGradient', 'radialGradient', 'stop', 'path', 'rect',
        'circle', 'ellipse', 'polygon', 'polyline', 'line', 'text', 'tspan'}
ATTRS = {'id', 'fill', 'stroke', 'stroke-width', 'stroke-linecap', 'stroke-linejoin',
         'stroke-dasharray', 'stroke-dashoffset', 'fill-rule', 'opacity', 'fill-opacity',
         'stroke-opacity', 'transform', 'd', 'x', 'y', 'x1', 'x2', 'y1', 'y2', 'dx', 'dy',
         'cx', 'cy', 'r', 'rx', 'ry', 'width', 'height', 'points', 'offset', 'stop-color',
         'stop-opacity', 'gradientUnits', 'gradientTransform', 'fx', 'fy', 'spreadMethod',
         'font-family', 'font-size', 'font-weight', 'text-anchor', 'dominant-baseline'}
SVG_NS = 'http://www.w3.org/2000/svg'
SYSTEM = '''You are an animation illustrator. Return only a JSON object describing a beautiful,
recognizable animated 2D scene that closely follows the user's subject, colors, action and style.
Never substitute a radar, orb or generic rings unless requested. Use a coherent designed palette,
rich layered silhouettes, expressive shapes, highlights, gradients and a clear composition.
Canvas viewBox dimensions are given by the user. All SVG coordinates use those dimensions.
JSON schema: {"background":"#101827","layers":[{"svg":"<g>...</g>","dx":0,"dy":8,
"rotate":0,"spin":0,"scale":0,"cx":320,"cy":320,"phase":0}]}.
Create 3 to 10 back-to-front layers with distinct scene elements and meaningful motion.
Each svg is an SVG fragment (no outer svg). Supported elements: g, defs, linearGradient,
radialGradient, stop, path, rect, circle, ellipse, polygon, polyline, line, text, tspan.
Use presentation attributes only, never CSS/style, script, image, href, filters, external files or URLs.
Gradients via fill="url(#uniqueId)" are allowed. SVG attributes must be valid XML with quoted values.
Motion is a seamless 3-second loop: dx/dy are sinusoidal displacement amplitudes in canvas units,
rotate is rocking amplitude in degrees, spin is full rotations in degrees (0, 360 or -360),
scale is sinusoidal scale amplitude (0 to 0.25); cx/cy are the layer's rotation/scale pivot;
phase is radians (e.g. opposite limbs phase 3.14159). At least one visible layer must move.
Keep background layers still. Use the user's requested action to design the moving layers.
Return compact JSON, no markdown. Keep the entire response under 2200 tokens. Use concise path data and no whitespace.'''


def _number(layer, key, default, limit):
    value = layer.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (float, int)):
        raise ValueError('Movimento inválido na cena gerada.')
    if not math.isfinite(value) or abs(value) > limit:
        raise ValueError('Movimento fora dos limites na cena gerada.')
    return float(value)


def validate_scene(scene):
    if not isinstance(scene, dict) or not re.fullmatch(r'#[0-9a-fA-F]{6}', str(scene.get('background', ''))):
        raise ValueError('A IA devolveu uma cena inválida. Tenta uma descrição mais concreta.')
    layers = scene.get('layers')
    if not isinstance(layers, list) or not 1 <= len(layers) <= 12:
        raise ValueError('Número de camadas inválido.')
    checked, nodes, moving = [], 0, False
    for layer in layers:
        if not isinstance(layer, dict):
            raise ValueError('Camada inválida.')
        fragment = layer.get('svg', '')
        if not isinstance(fragment, str) or len(fragment) > 50000 or '<!' in fragment or '<?' in fragment:
            raise ValueError('SVG inválido na cena gerada.')
        root = ET.fromstring(f'<svg xmlns="{SVG_NS}">{fragment}</svg>')
        if not len(root):
            raise ValueError('Camada vazia.')
        for element in list(root.iter())[1:]:
            nodes += 1
            tag = element.tag.removeprefix('{' + SVG_NS + '}')
            if nodes > 1500 or tag not in TAGS:
                raise ValueError('Elemento SVG não permitido.')
            for key, value in element.attrib.items():
                if key not in ATTRS or len(value) > 20000:
                    raise ValueError('Atributo SVG não permitido.')
                # No resource access: even local file references are forbidden.
                if any(c in value for c in ('\\', '\n', '\r')) or '@' in value:
                    raise ValueError('Referência SVG não permitida.')
                if 'url' in value.lower() and not re.fullmatch(r'url\(#[a-zA-Z_][\w-]*\)', value):
                    raise ValueError('Referência externa não permitida.')
        motion = {key: _number(layer, key, default, limit) for key, default, limit in (
            ('dx', 0, 640), ('dy', 0, 640), ('rotate', 0, 180), ('spin', 0, 360),
            ('scale', 0, .25), ('cx', 320, 1280), ('cy', 320, 1280), ('phase', 0, 7))}
        if motion['spin'] not in (-360, 0, 360):
            raise ValueError('A rotação deve fechar o loop.')
        moving |= any(motion[key] for key in ('dx', 'dy', 'rotate', 'spin', 'scale'))
        checked.append(dict(motion, svg=fragment))
    if not moving:
        raise ValueError('A IA devolveu uma cena sem movimento. Tenta novamente.')
    return dict(background=scene['background'], layers=checked)


def create_scene(prompt, width, height):
    import maestro as M
    import maestro_readonly as RO
    key = M._extract_deepseek_key()
    if not key:
        raise RuntimeError('DeepSeek não está configurado para criar cenas animadas.')
    payload = {
        'model': 'deepseek-flash', 'response_format': {'type': 'json_object'},
        'messages': [{'role': 'system', 'content': SYSTEM},
                     {'role': 'user', 'content': f'Canvas {width} x {height}. Description: {prompt}'}],
        'temperature': .85, 'max_tokens': 3500, 'thinking': {'type': 'disabled'},
    }
    for attempt in range(2):
        # API/network/budget failures propagate; only malformed artwork gets one repair.
        data = RO._request('POST', '/chat/completions', key, payload)
        choice = data['choices'][0]
        content = choice['message']['content']
        try:
            if choice.get('finish_reason') != 'stop':
                raise ValueError('A descrição da cena ficou incompleta.')
            return validate_scene(json.loads(content))
        except (ValueError, ET.ParseError) as exc:
            if attempt:
                raise ValueError('A IA não conseguiu criar uma cena válida. Tenta uma descrição mais simples.') from exc
            payload['messages'].extend([
                {'role': 'assistant', 'content': content[:40000]},
                {'role': 'user', 'content': 'Repair the JSON/SVG scene. Error: ' + str(exc)[:200] +
                 '. Use proper XML: close every g, defs, gradient and shape. Simplify to under 1800 tokens. Return JSON only.'}])
            payload['temperature'] = .2


def render_scene(scene, width, height):
    import cairosvg
    from PIL import Image
    frames = []
    for frame in range(48):
        t = frame / 48
        layers = []
        for layer in scene['layers']:
            wave = math.sin(t * math.tau + layer['phase'])
            x, y = layer['dx'] * wave, layer['dy'] * wave
            angle = layer['rotate'] * wave + layer['spin'] * t
            scale = 1 + layer['scale'] * wave
            cx, cy = layer['cx'], layer['cy']
            transform = f'translate({x} {y}) translate({cx} {cy}) rotate({angle}) scale({scale}) translate({-cx} {-cy})'
            layers.append(f'<g transform="{transform}">{layer["svg"]}</g>')
        svg = (f'<svg xmlns="{SVG_NS}" width="{width}" height="{height}" viewBox="0 0 {width} {height}">'
               f'<rect width="100%" height="100%" fill="{scene["background"]}"/>' + ''.join(layers) + '</svg>')
        png = cairosvg.svg2png(bytestring=svg.encode(), output_width=width, output_height=height)
        frames.append(Image.open(io.BytesIO(png)).convert('RGB'))
    # A shared, scene-specific palette avoids color flicker and the old 6x6x6 cube.
    sheet = Image.new('RGB', (width * 4, height))
    for slot, index in enumerate((0, 12, 24, 36)):
        sheet.paste(frames[index], (slot * width, 0))
    palette = sheet.quantize(colors=256, method=Image.Quantize.MEDIANCUT)
    indexed = [f.quantize(palette=palette, dither=Image.Dither.FLOYDSTEINBERG) for f in frames]
    output = io.BytesIO()
    # GIF units are 10 ms: alternate 60/70 ms for exactly 3 seconds at 16 fps.
    indexed[0].save(output, format='GIF', save_all=True, append_images=indexed[1:],
                    duration=[60, 60, 60, 70] * 12, loop=0, disposal=2, optimize=False)
    return output.getvalue()


def generate(prompt, aspect='1:1'):
    prompt = prompt.strip()
    if not prompt or len(prompt) > 2000:
        raise ValueError('Descreve a animação em 1 a 2000 caracteres.')
    if aspect not in ('1:1', '16:9', '9:16'):
        raise ValueError('Formato inválido.')
    if not RENDER_LOCK.acquire(blocking=False):
        raise RuntimeError('Já existe um GIF a ser criado. Aguarda a conclusão.')
    try:
        # Check dependencies before spending API budget.
        import cairosvg
        from PIL import Image
        width, height = {'1:1': (640, 640), '16:9': (640, 360), '9:16': (360, 640)}[aspect]
        scene = create_scene(prompt, width, height)
        return render_scene(scene, width, height), width, height
    finally:
        RENDER_LOCK.release()
