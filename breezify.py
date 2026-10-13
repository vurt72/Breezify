#!/usr/bin/env python3
"""
Breezify by Vurt (breezify.py) - realistic wind animation for Morrowind trees (OpenMW)
=======================================================================
Turns a static (or old-style animated) Morrowind tree .nif into a skinned, wind-animated tree:
  * the upper trunk bends while the base stays planted (4 trunk bones),
  * the crown moves in 8 directions x 3 heights (24 branch bones) with slow gusts, sway, bobbing, twist and flutter,
  * every leaf card / cluster follows its own branch bone mixed with a random neighbour -> leaves stay on the twigs
    but never move in lockstep,
  * a 120 s loop built from prime-numbered cycles, so the repeat is not noticeable.

Requirements: Python 3.8+ and numpy  (pip install numpy)

Usage:
  python breezify.py <file.nif | folder> [more files/folders] [options]

Options:
  -o, --out DIR     output folder (default: "wind_output" next to each input). Inputs are never overwritten.
  --sway F          trunk sway, fraction of tree height       (default 0.012)
  --branch F        branch motion, fraction of tree height    (default 0.010)
  --flutter F       fast leaf flutter, fraction of height     (default 0.004)
  --seed N          random seed (default: derived from the file name, so every tree model moves differently)
  --no-dist         do not write a static <name>_dist.nif copy for distant/paged trees
  --no-merge        keep every shape separate (default: shapes with identical materials are merged -> fewer draw calls)

Notes:
  * Leaves are detected as shapes with an NiAlphaProperty. Trees without any are skipped (use --force to animate anyway).
  * Collision (RootCollisionNode) is left untouched.
  * A static copy of the input is written as <name>_dist.nif (unless one exists next to the input or --no-dist):
    OpenMW uses _dist meshes for distant / object-paged trees, which should not carry the skinning.
  * Built and tested for OpenMW 0.51. The original Morrowind engine / MGE XE are untested.
  * OpenMW plays every copy of the same mesh in sync (OpenMW issue #6455); each tree MODEL gets its own pattern.
Credits: wind design by Vurt + Claude (Fallout 3 Animated Flora Overhaul skinwind v5, ported to Morrowind).
"""
import sys, os, io, struct, math, argparse, zlib, shutil
import numpy as np

# =====================================================================================================
# Minimal NIF 4.0.0.2 (Morrowind) reader / writer
# =====================================================================================================
class R:
    def __init__(s, b): s.b = b; s.p = 0
    def u(s, f):
        v = struct.unpack_from('<' + f, s.b, s.p); s.p += struct.calcsize('<' + f); return v
    def u1(s, f): return s.u(f)[0]
    def str_(s):
        n = s.u1('I'); v = s.b[s.p:s.p + n]; s.p += n; return v.decode('latin1')

class W:
    def __init__(s): s.o = io.BytesIO()
    def w(s, f, *v): s.o.write(struct.pack('<' + f, *v))
    def str_(s, t): b = t.encode('latin1'); s.w('I', len(b)); s.o.write(b)

NODE_TYPES = ('NiNode', 'NiBSAnimationNode', 'RootCollisionNode', 'NiBSParticleNode')

def r_net(r, d): d['name'] = r.str_(); d['extra'] = r.u1('i'); d['ctrl'] = r.u1('i')
def w_net(w, d): w.str_(d['name']); w.w('i', d['extra']); w.w('i', d['ctrl'])

def r_av(r, d):
    r_net(r, d)
    d['flags'] = r.u1('H'); d['trans'] = r.u('3f'); d['rot'] = r.u('9f'); d['scale'] = r.u1('f')
    d['vel'] = r.u('3f'); n = r.u1('I'); d['props'] = list(r.u('%di' % n)); d['hasbv'] = r.u1('I')
    if d['hasbv']:
        t = r.u1('I'); d['bvtype'] = t; d['bv'] = r.u('%df' % {0: 4, 1: 15, 2: 8, 5: 4}[t])
def w_av(w, d):
    w_net(w, d); w.w('H', d['flags']); w.w('3f', *d['trans']); w.w('9f', *d['rot']); w.w('f', d['scale'])
    w.w('3f', *d['vel']); w.w('I', len(d['props'])); [w.w('i', p) for p in d['props']]; w.w('I', d['hasbv'])
    if d['hasbv']: w.w('I', d['bvtype']); w.w('%df' % len(d['bv']), *d['bv'])

def r_node(r, d):
    r_av(r, d); n = r.u1('I'); d['children'] = list(r.u('%di' % n)); n = r.u1('I'); d['effects'] = list(r.u('%di' % n))
def w_node(w, d):
    w_av(w, d); w.w('I', len(d['children'])); [w.w('i', c) for c in d['children']]
    w.w('I', len(d['effects'])); [w.w('i', c) for c in d['effects']]

def r_lod(r, d):
    r_node(r, d); d['swidx'] = r.u1('I'); d['lodcenter'] = r.u('3f'); n = r.u1('I'); d['lodranges'] = [r.u('2f') for _ in range(n)]
def w_lod(w, d):
    w_node(w, d); w.w('I', d['swidx']); w.w('3f', *d['lodcenter']); w.w('I', len(d['lodranges'])); [w.w('2f', *x) for x in d['lodranges']]

def r_shape(r, d): r_av(r, d); d['data'] = r.u1('i'); d['skin'] = r.u1('i')
def w_shape(w, d): w_av(w, d); w.w('i', d['data']); w.w('i', d['skin'])

def r_geomdata(r, d):
    nv = r.u1('H'); d['nv'] = nv
    d['hasv'] = r.u1('I'); d['v'] = [r.u('3f') for _ in range(nv)] if d['hasv'] else []
    d['hasn'] = r.u1('I'); d['n'] = [r.u('3f') for _ in range(nv)] if d['hasn'] else []
    d['center'] = r.u('3f'); d['radius'] = r.u1('f')
    d['hasc'] = r.u1('I'); d['c'] = [r.u('4f') for _ in range(nv)] if d['hasc'] else []
    nuv = r.u1('H'); d['nuv'] = nuv; d['hasuv'] = r.u1('I')
    d['uv'] = [[r.u('2f') for _ in range(nv)] for _ in range(nuv)] if d['hasuv'] else []
def w_geomdata(w, d):
    nv = len(d['v']); w.w('H', nv); w.w('I', 1 if d['v'] else 0); [w.w('3f', *x) for x in d['v']]
    w.w('I', 1 if d['n'] else 0); [w.w('3f', *x) for x in d['n']]
    w.w('3f', *d['center']); w.w('f', d['radius'])
    w.w('I', 1 if d['c'] else 0); [w.w('4f', *x) for x in d['c']]
    w.w('H', len(d['uv'])); w.w('I', 1 if d['uv'] else 0)
    for s in d['uv']: [w.w('2f', *x) for x in s]

def r_tsdata(r, d):
    r_geomdata(r, d); nt = r.u1('H'); d['ntp'] = r.u1('I'); d['t'] = [r.u('3H') for _ in range(nt)]
    nm = r.u1('H'); d['match'] = []
    for _ in range(nm): k = r.u1('H'); d['match'].append(list(r.u('%dH' % k)))
def w_tsdata(w, d):
    w_geomdata(w, d); w.w('H', len(d['t'])); w.w('I', 3 * len(d['t'])); [w.w('3H', *x) for x in d['t']]
    w.w('H', len(d['match']))
    for g in d['match']: w.w('H', len(g)); [w.w('H', i) for i in g]

def r_stripsdata(r, d):   # converted to a triangle list on read (written back as NiTriShapeData)
    r_geomdata(r, d); r.u1('H'); ns = r.u1('H'); lens = [r.u1('H') for _ in range(ns)]
    tris = []
    for L in lens:
        s = r.u('%dH' % L)
        for k in range(L - 2):
            a, b, c = s[k], s[k + 1], s[k + 2]
            if a == b or b == c or a == c: continue
            tris.append((a, b, c) if k % 2 == 0 else (a, c, b))
    d['t'] = tris; d['match'] = []; d['type'] = 'NiTriShapeData'

def r_tctrl(r, d):
    d['next'] = r.u1('i'); d['cflags'] = r.u1('H'); d['freq'], d['phase'], d['start'], d['stop'] = r.u('4f'); d['target'] = r.u1('i')
def w_tctrl(w, d):
    w.w('i', d['next']); w.w('H', d['cflags']); w.w('4f', d['freq'], d['phase'], d['start'], d['stop']); w.w('i', d['target'])
def r_kfctrl(r, d): r_tctrl(r, d); d['data'] = r.u1('i')
def w_kfctrl(w, d): w_tctrl(w, d); w.w('i', d['data'])

def r_keygrp(r, fmt):
    n = r.u1('I'); g = {'interp': None, 'keys': []}
    if n:
        it = r.u1('I'); g['interp'] = it; k = struct.calcsize('<' + fmt) // 4
        per = 1 + k + (2 * k if it == 2 else 3 if it == 3 else 0); g['keys'] = [r.u('%df' % per) for _ in range(n)]
    return g
def w_keygrp(w, g):
    w.w('I', len(g['keys']))
    if g['keys']: w.w('I', g['interp']); [w.w('%df' % len(k), *k) for k in g['keys']]
def r_kfdata(r, d):
    n = r.u1('I'); d['rn'] = n; d['rtype'] = None; d['rkeys'] = []; d['xyz'] = None
    if n:
        t = r.u1('I'); d['rtype'] = t
        if t == 4: d['order'] = r.u1('f'); d['xyz'] = [r_keygrp(r, 'f') for _ in range(3)]
        else: d['rkeys'] = [r.u('%df' % (5 + (3 if t == 3 else 0))) for _ in range(n)]
    d['tr'] = r_keygrp(r, '3f'); d['sc'] = r_keygrp(r, 'f')
def w_kfdata(w, d):
    if d['rtype'] == 4:
        w.w('I', d['rn']); w.w('I', 4); w.w('f', d['order']); [w_keygrp(w, g) for g in d['xyz']]
    else:
        w.w('I', len(d['rkeys']))
        if d['rkeys']: w.w('I', d['rtype']); [w.w('%df' % len(k), *k) for k in d['rkeys']]
    w_keygrp(w, d['tr']); w_keygrp(w, d['sc'])

def r_flagprop(r, d): r_net(r, d); d['flags'] = r.u1('H')
def w_flagprop(w, d): w_net(w, d); w.w('H', d['flags'])
def r_alpha(r, d): r_net(r, d); d['flags'] = r.u1('H'); d['thresh'] = r.u1('B')
def w_alpha(w, d): w_net(w, d); w.w('H', d['flags']); w.w('B', d['thresh'])
def r_mat(r, d): r_net(r, d); d['flags'] = r.u1('H'); d['vals'] = r.u('14f')
def w_mat(w, d): w_net(w, d); w.w('H', d['flags']); w.w('14f', *d['vals'])
def r_stencil(r, d): r_net(r, d); d['flags'] = r.u1('H'); d['en'] = r.u1('B'); d['vals'] = r.u('7I')
def w_stencil(w, d): w_net(w, d); w.w('H', d['flags']); w.w('B', d['en']); w.w('7I', *d['vals'])
def r_vcol(r, d): r_net(r, d); d['flags'] = r.u1('H'); d['vc'] = r.u('2I')
def w_vcol(w, d): w_net(w, d); w.w('H', d['flags']); w.w('2I', *d['vc'])
def r_tex(r, d):
    r_net(r, d); d['flags'] = r.u1('H'); d['apply'] = r.u1('I'); cnt = r.u1('I'); d['slots'] = []
    for i in range(cnt):
        has = r.u1('I')
        if not has: d['slots'].append(None); continue
        td = {'src': r.u1('i'), 'rest': r.u('3I2hH')}
        if i == 5: td['bump'] = r.u('6f')
        d['slots'].append(td)
def w_tex(w, d):
    w_net(w, d); w.w('H', d['flags']); w.w('I', d['apply']); w.w('I', len(d['slots']))
    for i, td in enumerate(d['slots']):
        if td is None: w.w('I', 0); continue
        w.w('I', 1); w.w('i', td['src']); w.w('3I2hH', *td['rest'])
        if i == 5: w.w('6f', *td['bump'])
def r_src(r, d):
    r_net(r, d); d['ext'] = r.u1('B')
    if d['ext']: d['file'] = r.str_()
    else: d['unk'] = r.u1('B'); d['pix'] = r.u1('i')
    d['tail'] = r.u('3IB')
def w_src(w, d):
    w_net(w, d); w.w('B', d['ext'])
    if d['ext']: w.str_(d['file'])
    else: w.w('B', d['unk']); w.w('i', d['pix'])
    w.w('3IB', *d['tail'])
def r_sed(r, d): d['next'] = r.u1('i'); d['bytes'] = r.u1('I'); d['str'] = r.str_()
def w_sed(w, d): w.w('i', d['next']); w.w('I', d['bytes']); w.str_(d['str'])
def r_tked(r, d):
    d['next'] = r.u1('i'); d['bytes'] = r.u1('I'); d['unk'] = r.u1('I'); n = r.u1('I'); d['keys'] = [(r.u1('f'), r.str_()) for _ in range(n)]
def w_tked(w, d):
    w.w('i', d['next']); w.w('I', d['bytes']); w.w('I', d['unk']); w.w('I', len(d['keys'])); [(w.w('f', t), w.str_(s)) for t, s in d['keys']]
def r_skininst(r, d): d['data'] = r.u1('i'); d['skelroot'] = r.u1('i'); n = r.u1('I'); d['bones'] = list(r.u('%di' % n))
def w_skininst(w, d): w.w('i', d['data']); w.w('i', d['skelroot']); w.w('I', len(d['bones'])); [w.w('i', b) for b in d['bones']]
def r_skindata(r, d):
    d['rot'] = r.u('9f'); d['trans'] = r.u('3f'); d['scale'] = r.u1('f'); n = r.u1('I'); d['partition'] = r.u1('i'); d['bonedata'] = []
    for _ in range(n):
        b = {'rot': r.u('9f'), 'trans': r.u('3f'), 'scale': r.u1('f'), 'sphere': r.u('4f')}
        k = r.u1('H'); b['w'] = [r.u('Hf') for _ in range(k)]; d['bonedata'].append(b)
def w_skindata(w, d):
    w.w('9f', *d['rot']); w.w('3f', *d['trans']); w.w('f', d['scale']); w.w('I', len(d['bonedata'])); w.w('i', d['partition'])
    for b in d['bonedata']:
        w.w('9f', *b['rot']); w.w('3f', *b['trans']); w.w('f', b['scale']); w.w('4f', *b['sphere'])
        w.w('H', len(b['w'])); [w.w('Hf', i, x) for i, x in b['w']]

HANDLERS = {
    'NiLODNode': (r_lod, w_lod), **{t: (r_node, w_node) for t in NODE_TYPES},
    'NiTriShape': (r_shape, w_shape), 'NiTriStrips': (r_shape, w_shape),
    'NiTriShapeData': (r_tsdata, w_tsdata), 'NiTriStripsData': (r_stripsdata, w_tsdata),
    'NiKeyframeController': (r_kfctrl, w_kfctrl), 'NiKeyframeData': (r_kfdata, w_kfdata),
    'NiAlphaProperty': (r_alpha, w_alpha), 'NiMaterialProperty': (r_mat, w_mat), 'NiStencilProperty': (r_stencil, w_stencil),
    'NiZBufferProperty': (r_flagprop, w_flagprop), 'NiSpecularProperty': (r_flagprop, w_flagprop),
    'NiWireframeProperty': (r_flagprop, w_flagprop), 'NiDitherProperty': (r_flagprop, w_flagprop),
    'NiShadeProperty': (r_flagprop, w_flagprop), 'NiVertexColorProperty': (r_vcol, w_vcol),
    'NiTexturingProperty': (r_tex, w_tex), 'NiSourceTexture': (r_src, w_src),
    'NiStringExtraData': (r_sed, w_sed), 'NiTextKeyExtraData': (r_tked, w_tked),
    'NiSkinInstance': (r_skininst, w_skininst), 'NiSkinData': (r_skindata, w_skindata),
}
REFKEYS = ('extra', 'ctrl', 'data', 'skin', 'next', 'target', 'skelroot')
LISTKEYS = ('props', 'children', 'effects', 'bones')

class Unsupported(Exception): pass

def nif_read(path):
    b = open(path, 'rb').read()
    if not b.startswith(b'NetImmerse File Format, Version 4.0.0.2'): raise Unsupported('not a Morrowind (4.0.0.2) NIF')
    r = R(b); nl = b.index(b'\n') + 1; hdr = b[:nl]; r.p = nl
    ver, nb = r.u('2I'); blocks = []
    for i in range(nb):
        t = r.str_(); d = {'type': t}
        if t not in HANDLERS: raise Unsupported('block type %s is not supported' % t)
        HANDLERS[t][0](r, d)
        if t == 'NiTriStrips': d['type'] = 'NiTriShape'
        blocks.append(d)
    nr = r.u1('I'); roots = list(r.u('%di' % nr))
    return {'hdr': hdr, 'ver': ver, 'blocks': blocks, 'roots': roots}

def nif_write(nif):
    w = W(); w.o.write(nif['hdr']); w.w('2I', nif['ver'], len(nif['blocks']))
    for d in nif['blocks']: w.str_(d['type']); HANDLERS[d['type']][1](w, d)
    w.w('I', len(nif['roots'])); [w.w('i', x) for x in nif['roots']]
    return w.o.getvalue()

def remap(nif, remove):
    B = nif['blocks']; m = {}; j = 0
    for i in range(len(B)):
        if i in remove: continue
        m[i] = j; j += 1
    f = lambda x: -1 if x < 0 or x not in m else m[x]
    for i, d in enumerate(B):
        if i in remove: continue
        for k in REFKEYS:
            if k in d and isinstance(d[k], int): d[k] = f(d[k])
        for k in LISTKEYS:
            if k in d: d[k] = [x for x in (f(x) for x in d[k]) if x >= 0] if k == 'children' else [f(x) for x in d[k]]
        if d['type'] == 'NiTexturingProperty':
            for s in d['slots']:
                if s: s['src'] = f(s['src'])
    nif['blocks'] = [d for i, d in enumerate(B) if i not in remove]; nif['roots'] = [f(x) for x in nif['roots']]

def unreferenced(nif):
    B = nif['blocks']; refd = set(nif['roots'])
    for d in B:
        for k in REFKEYS:
            if k in d and isinstance(d[k], int) and d[k] >= 0: refd.add(d[k])
        for k in LISTKEYS: refd.update(x for x in d.get(k, []) if x >= 0)
        if d['type'] == 'NiTexturingProperty': refd.update(s['src'] for s in d['slots'] if s)
    return {i for i in range(len(B)) if i not in refd}

# =====================================================================================================
# Wind
# =====================================================================================================
LOOP = 120.0; RATE = 12.0; NSECT = 8; LEVELS = [0.0, 0.4, 0.7, 1.0]
IDENT = (1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0)

def is_kf(B, d): return d.get('ctrl', -1) >= 0 and B[d['ctrl']]['type'] == 'NiKeyframeController'
def is_leaf(B, sh): return any(B[p]['type'] == 'NiAlphaProperty' for p in sh['props'])

def merge_same_material(B, geo, log):
    """shapes with identical property lists (and the same attribute set) -> one shape. Returns removed blocks."""
    def psig(pi):   # property identity by CONTENT (vanilla meshes give every shape its own identical copies)
        d = B[pi]
        if d.get('ctrl', -1) >= 0: return ('unique', pi)
        if d['type'] == 'NiTexturingProperty':
            return (d['type'], d['flags'], d['apply'], tuple(None if s is None else
                    (B[s['src']].get('file', '#%d' % s['src']).lower() if s['src'] >= 0 else None, s['rest']) for s in d['slots']))
        return (d['type'],) + tuple((k, v if not isinstance(v, list) else tuple(v)) for k, v in sorted(d.items())
                                    if k not in ('name', 'extra', 'ctrl', 'type'))
    groups = {}
    for i in geo:
        sd = B[B[i]['data']]
        key = (tuple(sorted(psig(p) for p in B[i]['props'])), bool(sd['n']), bool(sd['c']), len(sd['uv']))
        groups.setdefault(key, []).append(i)
    removed = set()
    for key, members in groups.items():
        while len(members) > 1:
            first = members[0]; take = [first]; nv = len(geo[first][0])
            for m in members[1:]:
                if nv + len(geo[m][0]) < 65000: take.append(m); nv += len(geo[m][0])
            members = [m for m in members if m not in take]
            if len(take) < 2: break
            sd0 = B[B[first]['data']]; V, N, C, UV, T = [], [], [], [[] for _ in sd0['uv']], []
            for m in take:
                sd = B[B[m]['data']]; base = len(V); W_, N_, _ = geo[m]
                V += list(W_); N += list(N_) if N_ is not None else []; C += list(sd['c'])
                for k in range(len(UV)): UV[k] += list(sd['uv'][k])
                T += [(a + base, b + base, c + base) for a, b, c in sd['t']]
            sd0.update(v=[tuple(x) for x in V], c=C, uv=UV, t=T, match=[])
            geo[first] = (np.array(V), np.array(N) if N else None, geo[first][2])
            for m in take[1:]: removed |= {m, B[m]['data']}; del geo[m]
            log('    merged %d shapes with the same material -> 1' % len(take))
    return removed

def weld_ids(W):
    key = {}; out = np.zeros(len(W), int)
    for i, v in enumerate(np.round(W, 3)):
        out[i] = key.setdefault(tuple(v), len(key))
    return out

def build(src, dst, seed, SWAY, BRANCH, FLUT, merge=True, force=False, log=print):
    rng = np.random.default_rng(seed)
    n = nif_read(src); B = n['blocks']; root = n['roots'][0]
    if any(d['type'] == 'NiSkinInstance' for d in B): raise Unsupported('already skinned (wind already applied?)')
    if B[root]['type'] not in NODE_TYPES and B[root]['type'] != 'NiLODNode': raise Unsupported('root is not a node')
    # ---- LOD nodes: keep only the highest detail level (the wind mesh is the near mesh) ----
    for li, d in enumerate(B):
        if d['type'] == 'NiLODNode' and li != root and d['children']:
            for p in B:
                if 'children' in p and li in p['children']:
                    p['children'] = [d['children'][0] if c == li else c for c in p['children']]
    # ---- render shapes in root space (keyframed nodes at rest pose = identity rotation) ----
    shapes = {}; paths = {}
    def walk(i, M, T, s, path):
        d = B[i]
        if d['type'] == 'RootCollisionNode': return
        if i != root:
            Rm = np.eye(3) if is_kf(B, d) else np.array(d['rot']).reshape(3, 3)
            if is_kf(B, d) and B[B[d['ctrl']]['data']]['tr']['keys']:
                tr = np.array([k[1:4] for k in B[B[d['ctrl']]['data']]['tr']['keys']]).mean(0)
            else: tr = np.array(d['trans'])
            T = T + s * (M @ tr); M = M @ Rm; s = s * d['scale']
        if d['type'] == 'NiTriShape':
            if not (d['flags'] & 1) and i not in shapes and B[d['data']]['v']: shapes[i] = (M, T, s); paths[i] = path
            return
        kids = d.get('children', [])
        if d['type'] == 'NiLODNode' and kids: kids = kids[:1]          # highest detail level only
        for c in kids:
            if c >= 0: walk(c, M, T, s, path + [i])
    walk(root, np.eye(3), np.zeros(3), 1.0, [])
    if not shapes: raise Unsupported('no visible geometry')
    geo = {}
    for i, (M, T, s) in shapes.items():
        sd = B[B[i]['data']]
        Wv = (np.array(sd['v']) * s) @ M.T + T
        N = None
        if sd['n']:
            N = np.array(sd['n']) @ M.T; N = N / np.maximum(np.linalg.norm(N, axis=1, keepdims=True), 1e-9)
        geo[i] = (Wv, N, is_leaf(B, B[i]))
    if not any(g[2] for g in geo.values()) and not force:
        raise Unsupported('no leaf planes (no shape with an alpha property) - use --force to animate anyway')
    # ---- remove old controllers on shapes and their parents, re-parent shapes under the root ----
    rm = set()
    for i in geo:
        for pi in [i] + [p for p in paths[i] if p != root]:
            d = B[pi]
            if is_kf(B, d): c = d['ctrl']; rm |= {c, B[c]['data']}; d['ctrl'] = -1
    for d in B:
        if 'children' in d: d['children'] = [c for c in d['children'] if c not in geo]
    rnode = B[root]
    if rnode['type'] != 'NiBSAnimationNode':
        rnode['type'] = 'NiBSAnimationNode'; rnode['flags'] = 0x20; rnode.pop('swidx', None)
    if merge: rm |= merge_same_material(B, geo, log)
    allw = np.vstack([g[0] for g in geo.values()])
    fol = [i for i in geo if geo[i][2]] or list(geo)
    fw = np.vstack([geo[i][0] for i in fol])
    bark = [geo[i][0] for i in geo if not geo[i][2]]
    axis = np.median((np.vstack(bark) if bark else fw)[:, :2], 0)
    zmin, zmax = allw[:, 2].min(), allw[:, 2].max(); H = max(zmax - zmin, 1.0)
    Rr = max(np.percentile(np.linalg.norm(fw[:, :2] - axis, axis=1), 95), 1e-3)
    for i in geo:
        Wv, N, _ = geo[i]; sh = B[i]; sd = B[sh['data']]
        sd['v'] = [tuple(map(float, v)) for v in Wv]
        sd['n'] = [tuple(map(float, v)) for v in N] if N is not None else []
        c = (Wv.min(0) + Wv.max(0)) / 2; sd['center'] = tuple(map(float, c))
        sd['radius'] = float(np.linalg.norm(Wv - c, axis=1).max() + SWAY * H + 3 * BRANCH * H)
        sh.update(trans=(0.0, 0.0, 0.0), rot=IDENT, scale=1.0, hasbv=0)
        rnode['children'].append(i)

    # ---- bones + motion ----
    NT = len(LEVELS)
    bone_names = ['WindTrunk%d' % j for j in range(NT)] + ['WindBranch%d_%d' % (s_, j) for s_ in range(NSECT) for j in range(1, NT)]
    def bone_id(kind, s_, j):
        if kind == 't' or j == 0: return j
        return NT + s_ * (NT - 1) + (j - 1)
    sect_ang = (np.arange(NSECT) + 0.5) * 2 * np.pi / NSECT
    T_ = np.arange(0, LOOP + 1e-6, 1.0 / RATE)
    def wave(ks, amps):
        o = np.zeros_like(T_)
        for k, a in zip(ks, amps): o += a * np.sin(2 * np.pi * k * T_ / LOOP + rng.uniform(0, 2 * np.pi))
        return o
    gust = 0.55 + 0.45 * np.clip(wave([3, 7], [0.6, 0.4]), -1, 1)
    swx = gust * wave([19, 31], [0.6, 0.4]); swy = gust * 0.8 * wave([23, 37], [0.6, 0.4])
    disp = np.zeros((len(bone_names), len(T_), 3))
    for j, hl in enumerate(LEVELS):
        disp[j, :, 0] = SWAY * H * hl ** 2 * swx; disp[j, :, 1] = SWAY * H * hl ** 2 * swy
    up = np.array([0, 0, 1.0])
    for s_ in range(NSECT):
        radu = np.array([math.cos(sect_ang[s_]), math.sin(sect_ang[s_]), 0]); tang = np.array([-radu[1], radu[0], 0])
        for j in range(1, NT):
            bi = bone_id('b', s_, j)
            bob = gust * wave([41, 67, 89], [0.5, 0.3, 0.2]); tw = gust * wave([47, 73, 97], [0.5, 0.3, 0.2])
            fl = gust ** 1.5 * wave([113, 151], [0.5, 0.5]); fd = rng.normal(size=3); fd /= np.linalg.norm(fd)
            disp[bi] = disp[j] + (1.25 * BRANCH * H * bob)[:, None] * (0.85 * up + 0.15 * radu) \
                       + (0.95 * BRANCH * H * tw)[:, None] * (0.7 * tang + 0.3 * up) \
                       + (0.9 * FLUT * H * fl)[:, None] * (0.6 * up + 0.4 * fd)
    disp[:, -1] = disp[:, 0]
    bone_blocks = []
    for nm in bone_names:
        B.append({'type': 'NiNode', 'name': nm, 'extra': -1, 'ctrl': -1, 'flags': 0x0c, 'trans': (0.0, 0.0, 0.0), 'rot': IDENT,
                  'scale': 1.0, 'vel': (0.0, 0.0, 0.0), 'props': [], 'hasbv': 0, 'children': [], 'effects': []})
        bone_blocks.append(len(B) - 1)
    rnode['children'] += bone_blocks

    def weights(Wv, secov):
        h = np.clip((Wv[:, 2] - zmin) / H, 0, 1)
        rad = Wv[:, :2] - axis; r = np.linalg.norm(rad, axis=1)
        ramp = np.clip((h - 0.05) / 0.20, 0, 1); ramp = ramp * ramp * (3 - 2 * ramp)
        b = np.clip(r / Rr, 0, 1) ** 1.5 * ramp
        ang = np.mod(np.arctan2(rad[:, 1], rad[:, 0]), 2 * np.pi); sec = np.minimum((ang / (2 * np.pi) * NSECT).astype(int), NSECT - 1)
        if secov is not None: sec = np.where(secov >= 0, secov, sec)
        j = np.clip(np.searchsorted(LEVELS, h, side='right') - 1, 0, NT - 2)
        f = (h - np.array(LEVELS)[j]) / (np.array(LEVELS)[j + 1] - np.array(LEVELS)[j])
        out = []
        for k in range(len(Wv)):
            acc = {}
            for bi, w in ((bone_id('t', 0, j[k]), (1 - b[k]) * (1 - f[k])), (bone_id('t', 0, j[k] + 1), (1 - b[k]) * f[k]),
                          (bone_id('b', sec[k], j[k]), b[k] * (1 - f[k])), (bone_id('b', sec[k], j[k] + 1), b[k] * f[k])):
                if w > 1e-5: acc[bi] = acc.get(bi, 0) + w
            tot = sum(acc.values()); out.append({bi: w / tot for bi, w in acc.items()})
        return out

    nverts = 0
    for i in geo:
        Wv, N, leaf = geo[i]; sd = B[B[i]['data']]; nv = len(Wv); nverts += nv
        secov = None; lab = None
        if leaf:   # leaf cards / clusters = connected components (by welded position)
            par_ = np.arange(nv)
            def find(x):
                while par_[x] != x: par_[x] = par_[par_[x]]; x = par_[x]
                return x
            for a, b_, c_ in sd['t']:
                ra = find(a); par_[find(b_)] = ra; par_[find(c_)] = ra
            first = {}
            for v, k in enumerate(weld_ids(Wv)):
                if k in first: par_[find(v)] = find(first[k])
                else: first[k] = v
            lab = np.array([find(v) for v in range(nv)])
            secov = np.full(nv, -1)
            for L_ in np.unique(lab):
                m = lab == L_
                if m.sum() > 0.3 * nv: continue
                cen = Wv[m].mean(0); a_ = math.atan2(cen[1] - axis[1], cen[0] - axis[0]) % (2 * math.pi)
                secov[m] = min(int(a_ / (2 * math.pi) * NSECT), NSECT - 1)
        wts = weights(Wv, secov)
        if leaf:
            h_all = np.clip((Wv[:, 2] - zmin) / H, 0, 1)
            for L_ in np.unique(lab):
                m = np.where(lab == L_)[0]
                if secov[m[0]] < 0: continue
                sc = secov[m[0]]; hc = h_all[m].mean()
                jn = int(np.clip(np.argmin(np.abs(np.array(LEVELS[1:]) - hc)) + 1, 1, NT - 1))
                choices = [((sc - 1) % NSECT, jn), ((sc + 1) % NSECT, jn)] + ([(sc, jn - 1)] if jn > 1 else []) + ([(sc, jn + 1)] if jn < NT - 1 else [])
                s2, j2 = choices[rng.integers(len(choices))]
                alpha = rng.uniform(0.25, 0.5); kamp = rng.uniform(0.7, 1.3)
                b1 = bone_id('b', sc, jn); b2 = bone_id('b', s2, j2)
                for v in m:
                    w = wts[v]; trunk = {bi: wv for bi, wv in w.items() if bi < NT}
                    bsum = sum(wv for bi, wv in w.items() if bi >= NT); bb = min(1.0, bsum * kamp); tsum = sum(trunk.values())
                    nw = {bi: wv / tsum * (1 - bb) for bi, wv in trunk.items()} if tsum > 1e-9 else ({0: 1 - bb} if bb < 1 else {})
                    if bb > 1e-6: nw[b1] = nw.get(b1, 0) + bb * (1 - alpha); nw[b2] = nw.get(b2, 0) + bb * alpha
                    tot = sum(nw.values()); wts[v] = {bi: wv / tot for bi, wv in nw.items() if wv > 1e-6}
        for v in range(nv):
            w = {bi: x for bi, x in wts[v].items() if x >= 0.03} or {max(wts[v], key=wts[v].get): 1.0}
            if len(w) > 4: w = dict(sorted(w.items(), key=lambda x: -x[1])[:4])
            t_ = sum(w.values()); wts[v] = {bi: x / t_ for bi, x in w.items()}
        used = sorted({bi for w in wts for bi in w}); bonedata = []
        for bi in used:
            wl = [(vi, float(w[bi])) for vi, w in enumerate(wts) if bi in w]
            pts = Wv[[vi for vi, _ in wl]]; cc = pts.mean(0)
            bonedata.append({'rot': IDENT, 'trans': (0.0, 0.0, 0.0), 'scale': 1.0,
                             'sphere': (*map(float, cc), float(np.linalg.norm(pts - cc, axis=1).max())), 'w': wl})
        B.append({'type': 'NiSkinData', 'rot': IDENT, 'trans': (0.0, 0.0, 0.0), 'scale': 1.0, 'partition': -1, 'bonedata': bonedata})
        B.append({'type': 'NiSkinInstance', 'data': len(B) - 1, 'skelroot': root, 'bones': [bone_blocks[bi] for bi in used]})
        B[i]['skin'] = len(B) - 1
    for k, bb in enumerate(bone_blocks):
        if np.abs(disp[k]).max() < 1e-9: continue
        keys = [(float(t), *map(float, p)) for t, p in zip(T_, disp[k])]
        B.append({'type': 'NiKeyframeData', 'rn': 0, 'rtype': None, 'rkeys': [], 'xyz': None,
                  'tr': {'interp': 1, 'keys': keys}, 'sc': {'interp': None, 'keys': []}})
        B.append({'type': 'NiKeyframeController', 'next': -1, 'cflags': 8, 'freq': 1.0, 'phase': 0.0, 'start': 0.0,
                  'stop': LOOP, 'target': bb, 'data': len(B) - 1})
        B[bb]['ctrl'] = len(B) - 1
    if rm: remap(n, rm)
    while True:   # empty leftover nodes (old leaf parents), dangling blocks
        B = n['blocks']
        empty = {i for i, d in enumerate(B) if d['type'] in ('NiNode', 'NiBSAnimationNode', 'NiLODNode') and i != n['roots'][0]
                 and not d['children'] and d['ctrl'] < 0 and not d['name'].startswith('Wind')}
        u = unreferenced(n)
        if not empty and not u: break
        for d in B:
            if 'children' in d: d['children'] = [c for c in d['children'] if c not in empty]
        remap(n, empty | u)
    data = nif_write(n); nif_read_bytes_check(data)
    open(dst, 'wb').write(data)
    log('    H=%.0f units, %d skinned shape(s), %d vertices, top sway %.1f, branch %.1f units, %d KB' % (
        H, len(geo), nverts, np.abs(disp[NT - 1]).max(),
        np.abs(disp[NT:] - disp[[bone_id('t', 0, j) for s_ in range(NSECT) for j in range(1, NT)]]).max(), len(data) // 1024))

def nif_read_bytes_check(data):
    tmp = R(data); nl = data.index(b'\n') + 1; tmp.p = nl; ver, nb = tmp.u('2I')
    for _ in range(nb):
        t = tmp.str_(); HANDLERS[t][0](tmp, {})
    nr = tmp.u1('I'); tmp.u('%di' % nr)
    assert tmp.p == len(data), 'written file does not re-read cleanly'


# =====================================================================================================
# Optimized static _dist mesh (distant / object-paged trees)
# =====================================================================================================
_MO = None
def _meshopt():
    """meshoptimizer (github.com/zeux/meshoptimizer, MIT) compiled as meshopt.dll / libmeshopt.so next to this script"""
    global _MO
    if _MO is None:
        import ctypes as C
        here = os.path.dirname(os.path.abspath(__file__))
        name = 'meshopt.dll' if os.name == 'nt' else 'libmeshopt.so'
        path = os.path.join(here, name)
        if not os.path.exists(path): _MO = False; return _MO
        lib = C.CDLL(path); f = lib.meshopt_simplifyWithAttributes
        P = C.POINTER
        f.argtypes = [P(C.c_uint), P(C.c_uint), C.c_size_t, P(C.c_float), C.c_size_t, C.c_size_t, P(C.c_float), C.c_size_t,
                      P(C.c_float), C.c_size_t, P(C.c_ubyte), C.c_size_t, C.c_float, C.c_uint, P(C.c_float)]
        f.restype = C.c_size_t; _MO = (C, f)
    return _MO

def simplify(V, UV, N, T, target_tris, max_error=0.02):
    """seam-aware simplification that only removes vertices (kept vertices keep their exact position/UV/normal).
    Returns the new triangle list (indices into the ORIGINAL vertex arrays)."""
    mo = _meshopt()
    if not mo or target_tris >= len(T): return T
    C, f = mo
    V = np.ascontiguousarray(V, np.float32); idx = np.ascontiguousarray(np.array(T, np.uint32).ravel())
    attrs = [np.asarray(UV, np.float32)]; wts = [1.0, 1.0]
    if N is not None: attrs.append(np.asarray(N, np.float32)); wts += [0.5, 0.5, 0.5]
    A = np.ascontiguousarray(np.hstack(attrs), np.float32); Wt = np.ascontiguousarray(np.array(wts, np.float32))
    dest = np.zeros(len(idx), np.uint32); err = C.c_float(0)
    P = lambda a, t: a.ctypes.data_as(C.POINTER(t))
    k = f(P(dest, C.c_uint), P(idx, C.c_uint), len(idx), P(V, C.c_float), len(V), 12, P(A, C.c_float), A.shape[1] * 4,
          P(Wt, C.c_float), A.shape[1], None, int(target_tris) * 3, float(max_error), 0, C.byref(err))
    out = dest[:k].reshape(-1, 3)
    return [tuple(int(x) for x in t) for t in out if len(set(t)) == 3]

def build_dist(src, dst, mge=False, log=print):
    """static distant mesh: no collision, no animation, one shape per material, trunk/branches ~30%, dense leaves ~50%.
    mge=True: leaves get real back faces (MGE XE ignores two-sided at distance) and the two-sided property is dropped."""
    n = nif_read(src); B = n['blocks']; root = n['roots'][0]
    for li, d in enumerate(B):
        if d['type'] == 'NiLODNode' and li != root and d['children']:
            for p in B:
                if 'children' in p and li in p['children']:
                    p['children'] = [d['children'][0] if c == li else c for c in p['children']]
    items = []
    def walk(i, M, T, s):
        d = B[i]
        if d['type'] == 'RootCollisionNode': return
        if i != root:
            Rm = np.eye(3) if is_kf(B, d) else np.array(d['rot']).reshape(3, 3)
            if is_kf(B, d) and B[B[d['ctrl']]['data']]['tr']['keys']:
                tr = np.array([k[1:4] for k in B[B[d['ctrl']]['data']]['tr']['keys']]).mean(0)
            else: tr = np.array(d['trans'])
            T = T + s * (M @ tr); M = M @ Rm; s = s * d['scale']
        if d['type'] == 'NiTriShape':
            if not (d['flags'] & 1) and B[d['data']]['v'] and d['skin'] < 0: items.append((i, M, T, s))
            return
        kids = d.get('children', [])
        if d['type'] == 'NiLODNode' and kids: kids = kids[:1]
        for c in kids:
            if c >= 0: walk(c, M, T, s)
    walk(root, np.eye(3), np.zeros(3), 1.0)
    if not items: raise Unsupported('no geometry for a _dist mesh')
    def psig(pi):
        d = B[pi]
        if d['type'] == 'NiTexturingProperty':
            return (d['type'], d['flags'], d['apply'], tuple(None if s_ is None else
                    (B[s_['src']].get('file', '#%d' % s_['src']).lower() if s_['src'] >= 0 else None, s_['rest']) for s_ in d['slots']))
        return (d['type'],) + tuple((k, v if not isinstance(v, list) else tuple(v)) for k, v in sorted(d.items())
                                    if k not in ('name', 'extra', 'ctrl', 'type'))
    groups = {}
    for i, M, T, s in items:
        sd = B[B[i]['data']]
        key = (tuple(sorted(psig(p) for p in B[i]['props'])), bool(sd['n']), bool(sd['c']), min(len(sd['uv']), 1))
        groups.setdefault(key, []).append((i, M, T, s))
    out = [{'type': 'NiNode', 'name': os.path.basename(dst)[:-4], 'extra': -1, 'ctrl': -1, 'flags': 0, 'trans': (0.0, 0.0, 0.0),
            'rot': IDENT, 'scale': 1.0, 'vel': (0.0, 0.0, 0.0), 'props': [], 'hasbv': 0, 'children': [], 'effects': []}]
    copied = {}
    def copy_prop(pi):
        if pi in copied: return copied[pi]
        d = dict(B[pi]); d['ctrl'] = -1; d['extra'] = -1
        if d['type'] == 'NiTexturingProperty':
            d['slots'] = [None if s_ is None else dict(s_) for s_ in d['slots']]
            for s_ in d['slots']:
                if s_ and s_['src'] >= 0:
                    src_ = dict(B[s_['src']]); src_['ctrl'] = -1; src_['extra'] = -1; out.append(src_); s_['src'] = len(out) - 1
        out.append(d); copied[pi] = len(out) - 1; return copied[pi]
    t_in = t_out = 0
    for key, members in groups.items():
        leaf = any(B[p]['type'] == 'NiAlphaProperty' for p in B[members[0][0]]['props'])
        group_tris = sum(len(B[B[m[0]]['data']]['t']) for m in members)
        V, N, Cc, UV, Tr = [], [], [], [], []
        hasn, hasc = key[1], key[2]
        for i, M, T, s in members:
            sd = B[B[i]['data']]; t_in += len(sd['t'])
            Wv = (np.array(sd['v']) * s) @ M.T + T
            base = len(V); V += [tuple(map(float, x)) for x in Wv]
            UV += [tuple(map(float, x)) for x in (sd['uv'][0] if sd['uv'] else np.zeros((len(Wv), 2)))]
            if hasn:
                Nn = np.array(sd['n']) @ M.T; Nn = Nn / np.maximum(np.linalg.norm(Nn, axis=1, keepdims=True), 1e-9)
                N += [tuple(map(float, x)) for x in Nn]
            if hasc: Cc += [tuple(x) for x in sd['c']]
            Tr += [(a + base, b + base, c + base) for a, b, c in sd['t']]
        # reduce the merged group: trunk/branches 30% (50% if small), dense foliage 50%
        ratio = (0.5 if group_tris > 1000 else None) if leaf else ((0.3 if group_tris > 600 else 0.5) if group_tris > 150 else None)
        if ratio:
            Tr = simplify(np.array(V), np.array(UV), np.array(N) if hasn else None, Tr, len(Tr) * ratio)
            used = sorted({v for t in Tr for v in t}); rmp = {v: k for k, v in enumerate(used)}
            V = [V[v] for v in used]; UV = [UV[v] for v in used]
            if hasn: N = [N[v] for v in used]
            if hasc: Cc = [Cc[v] for v in used]
            Tr = [(rmp[a], rmp[b], rmp[c]) for a, b, c in Tr]
        props = [p for p in B[members[0][0]]['props']]
        if mge and leaf:      # real back faces, drop two-sided
            nv = len(V); V += V; UV += UV; Cc += Cc
            if hasn: N += [tuple(-x for x in v) for v in N]
            Tr += [(a + nv, c + nv, b + nv) for a, b, c in Tr]
            props = [p for p in props if B[p]['type'] != 'NiStencilProperty']
        elif mge:
            props = [p for p in props if B[p]['type'] != 'NiStencilProperty']
        # split if a merged group exceeds the 16-bit vertex limit
        chunks = [(V, N, Cc, UV, Tr)] if len(V) < 65000 else None
        if chunks is None:
            chunks = []; cur = [[], [], [], [], []]; m_ = {}
            for t in Tr:
                if len(cur[0]) > 64000: chunks.append(tuple(cur)); cur = [[], [], [], [], []]; m_ = {}
                nt = []
                for v in t:
                    if v not in m_:
                        m_[v] = len(cur[0]); cur[0].append(V[v]); cur[3].append(UV[v])
                        if hasn: cur[1].append(N[v])
                        if hasc: cur[2].append(Cc[v])
                    nt.append(m_[v])
                cur[4].append(tuple(nt))
            chunks.append(tuple(cur))
        for V_, N_, C_, UV_, T_ in chunks:
            Va = np.array(V_); ctr = (Va.min(0) + Va.max(0)) / 2
            out.append({'type': 'NiTriShapeData', 'nv': len(V_), 'v': V_, 'n': N_, 'center': tuple(map(float, ctr)),
                        'radius': float(np.linalg.norm(Va - ctr, axis=1).max()), 'c': C_, 'uv': [UV_], 't': T_, 'match': []})
            di = len(out) - 1
            out.append({'type': 'NiTriShape', 'name': B[members[0][0]]['name'], 'extra': -1, 'ctrl': -1, 'flags': 0,
                        'trans': (0.0, 0.0, 0.0), 'rot': IDENT, 'scale': 1.0, 'vel': (0.0, 0.0, 0.0),
                        'props': [copy_prop(p) for p in props], 'hasbv': 0, 'data': di, 'skin': -1})
            out[0]['children'].append(len(out) - 1); t_out += len(T_)
    nd = {'hdr': n['hdr'], 'ver': n['ver'], 'blocks': out, 'roots': [0]}
    data = nif_write(nd); nif_read_bytes_check(data); open(dst, 'wb').write(data)
    log('    + %s: %d -> %d triangles, %d shape(s)%s%s' % (os.path.basename(dst), t_in, t_out, len(out[0]['children']),
        ', leaves with back faces (MGE XE)' if mge else '', '' if _meshopt() else ' (meshopt library not found: no reduction)'))

def main():
    ap = argparse.ArgumentParser(description='Skinned wind animation for Morrowind tree meshes (OpenMW).')
    ap.add_argument('inputs', nargs='*'); ap.add_argument('-o', '--out')
    ap.add_argument('--sway', type=float, default=0.012); ap.add_argument('--branch', type=float, default=0.010)
    ap.add_argument('--flutter', type=float, default=0.004); ap.add_argument('--seed', type=int)
    ap.add_argument('--no-dist', action='store_true'); ap.add_argument('--no-merge', action='store_true')
    ap.add_argument('--force', action='store_true'); ap.add_argument('--mge', action='store_true')
    a = ap.parse_args()
    if not a.inputs:
        print('Breezify by Vurt\n\nDrag one or more tree .nif files (or a folder with them) onto Breezify.bat\n'
              '(or Breezify_mgexe.bat if you use MGE XE distant land).\n'
              'The animated trees are written to a "wind_output" folder next to them; your originals are not changed.\n'
              'See README.txt for options.')
        return
    files = []
    for p in a.inputs:
        if os.path.isdir(p):
            files += [os.path.join(p, f) for f in sorted(os.listdir(p)) if f.lower().endswith('.nif') and not f.lower().endswith('_dist.nif')]
        elif os.path.isfile(p): files.append(p)
        else: print('not found:', p)
    ok = skipped = 0
    for f in files:
        out = a.out or os.path.join(os.path.dirname(os.path.abspath(f)), 'wind_output')
        os.makedirs(out, exist_ok=True)
        dst = os.path.join(out, os.path.basename(f))
        if os.path.abspath(dst) == os.path.abspath(f): print('refusing to overwrite the input:', f); continue
        seed = a.seed if a.seed is not None else zlib.crc32(os.path.basename(f).lower().encode()) % 100000
        print(os.path.basename(f))
        try:
            build(f, dst, seed, a.sway, a.branch, a.flutter, merge=not a.no_merge, force=a.force)
            ok += 1
            if not a.no_dist:
                dn = os.path.basename(f)[:-4] + '_dist.nif'
                if os.path.exists(os.path.join(os.path.dirname(f), dn)):
                    print('    (the tree already has its own %s - kept, not replaced)' % dn)
                else:
                    build_dist(f, os.path.join(out, dn), mge=a.mge)
        except Unsupported as e:
            skipped += 1; print('    skipped:', e)
    print('\ndone: %d animated, %d skipped -> copy the output .nif files into Data Files\\meshes\\<same folder>' % (ok, skipped))

if __name__ == '__main__':
    main()
