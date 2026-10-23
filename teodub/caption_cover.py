"""Where a video's own burned-in captions are while each Vietnamese line is on screen.

A caption is light text with a darker edge on both sides of each stroke that stays still for a moment, while a
handheld background moves.
Two frames PAIR_S apart are compared at a few moments of every line; what is caption-like in both is kept.
All lines together teach the video's caption rows and typical width, which filters out still scenery; a line's box
keeps only what lies on those rows (or forms another whole text line), not scenery merged above or below. A line whose
caption was not seen gets the typical caption box of the video, so a caption is never left bare; a video without
captions gets no boxes. The picture is decoded once from start to end and analysed at most ANALYSIS_WIDTH wide.
Checked on five real videos from four channels (21/10/2026): small white captions, white/yellow captions on a
black bar, very large bold captions and captions with a gap between words.
"""
import numpy as np
from scipy import ndimage

ROI_TOP = .60              # Captions are searched in the bottom 40% of the picture.
ANALYSIS_WIDTH = 960       # Frames are analysed at most this wide; boxes are scaled back to the picture.
LIGHT, CONTRAST = 185, 75  # Caption pixels: at least this light and this much lighter than their surroundings...
STROKE_REACH = 100         # ...on both sides within width/STROKE_REACH pixels (9 at 960: strokes up to ~16 px).
PAIR_S = .2                # A caption stays put between two frames this far apart.
CENTRE = .15               # Captions are centred: a line's middle is within this share of the width from the centre.
WORD_GAP = .10             # Parts of one caption line may be this far apart (a space or punctuation)...
WORD_HEIGHT = .5           # ...if each part fills at least this share of the line's height...
WORD_DENSITY = .08         # ...with at least this share of its area in still light strokes.
MIN_LINES = 3              # Fewer caption sightings than this: the video is treated as having no captions.
LONG_EDGE_HEIGHT = .16     # A light vertical edge taller than this share of the picture is scenery, not a caption.
LONG_EDGE_WIDTH = .25      # Likewise a light horizontal edge wider than this share of the picture.
TALL_BAND = .14            # A run of rows taller than this share of the picture is split before it is judged.
ROW_TOLERANCE = .3         # A line's caption may sit this many lines above or below the video's caption rows.
SECOND_LINE = .75          # Beyond them, only a run of rows at least this many lines tall counts as caption...
DENSE_ROWS = .5            # ...with at least this share of the still pixels per row of the caption rows.


def _mask(rgb):
    """Light stroke pixels: darker by CONTRAST on BOTH sides within the stroke reach, across (left and right) or
    along (above and below). A caption's light text with its dark outline passes; the edge of a white table square,
    plate or cup is dark on one side only and does not (22/10/2026: a still scene of video 10 merged a checkerboard
    table, a snack bag and a cup into the caption; the caption alone remained)."""
    luma = (rgb[..., 0].astype(np.int32)*299+rgb[..., 1].astype(np.int32)*587+rgb[..., 2].astype(np.int32)*114)//1000
    reach = max(4, rgb.shape[1]//STROKE_REACH)
    stroke = np.zeros(luma.shape, bool)
    for axis in (0, 1):
        before = ndimage.minimum_filter1d(luma, size=reach, axis=axis, origin=(reach-1)//2)
        after = ndimage.minimum_filter1d(luma, size=reach, axis=axis, origin=-(reach//2))
        stroke |= (luma-before >= CONTRAST) & (luma-after >= CONTRAST)
    return (luma >= LIGHT) & stroke


def _long_runs(mask, length, axis):
    """Pixels on a straight run at least `length` long along `axis` (a 1-D opening, done with fast window filters)."""
    full = ndimage.minimum_filter1d(mask, size=length, axis=axis)
    return ndimage.maximum_filter1d(full, size=length, axis=axis)


def _without_long_edges(still, width, height):
    """Drop long straight light edges (poles, door frames, table edges): no caption stroke is that long."""
    cells = still.view(np.uint8)
    edges = (_long_runs(cells, max(3, int(height*LONG_EDGE_HEIGHT)), 0) |
             _long_runs(cells, max(3, int(width*LONG_EDGE_WIDTH)), 1))
    if not edges.any():
        return still
    return still & ~ndimage.maximum_filter(edges, size=5).astype(bool)


def _row_bands(rows, threshold, height, depth=0):
    """Runs of rows with at least `threshold` still pixels. A run taller than a caption line is split again with a
    higher threshold, which separates dense caption rows from sparser still scenery touching them."""
    labels, found = ndimage.label(ndimage.binary_closing(rows >= threshold, structure=np.ones(max(3, height//160))))
    for index in range(1, found+1):
        band = np.nonzero(labels == index)[0]
        y0, y1 = int(band.min()), int(band.max())
        if y1-y0 > height*TALL_BAND and depth < 4:
            higher = max(threshold+1, int(np.percentile(rows[y0:y1+1], 50)))
            inner = np.zeros_like(rows)
            inner[y0:y1+1] = rows[y0:y1+1]
            parts = list(_row_bands(inner, higher, height, depth+1))
            if parts:
                yield from parts
                continue
        yield y0, y1


def _candidates(still, width, height, top):
    """Caption-like lines (one or two rows of text) in a mask of still pixels, in analysis pixels."""
    still = _without_long_edges(still, width, height)
    rows = still.sum(axis=1)
    result = []
    for y0, y1 in _row_bands(rows, max(2, width//480), height):
        if not height*.012 <= y1-y0 <= height*.25:  # One or two lines, from small to very large lettering.
            continue
        runs, count = ndimage.label(ndimage.binary_closing(still[y0:y1+1].sum(axis=0) >= 1,
                                                           structure=np.ones(max(3, int(width*.03)))))
        if not count:
            continue
        middle = width//2
        spans, wordlike = {}, set()
        for run in range(1, count+1):
            xs = np.nonzero(runs == run)[0]
            spans[run] = (int(xs.min()), int(xs.max()))
            # Text fills most of the line's height with dense strokes; a thin rim or wire does not.
            piece = still[y0:y1+1, spans[run][0]:spans[run][1]+1]
            filled = np.nonzero(piece.any(axis=1))[0]
            if (filled.max()-filled.min()+1 >= (y1-y0+1)*WORD_HEIGHT and
                    piece.sum() >= piece.size*WORD_DENSITY):
                wordlike.add(run)
        nearest = min(spans, key=lambda run: 0 if spans[run][0] <= middle <= spans[run][1]
                      else min(abs(spans[run][0]-middle), abs(spans[run][1]-middle)))
        x0, x1 = spans.pop(nearest)
        grown = True
        while grown:
            grown = False
            for run, (a, b) in list(spans.items()):
                if run in wordlike and a-x1 <= width*WORD_GAP and x0-b <= width*WORD_GAP:
                    x0, x1 = min(x0, a), max(x1, b)
                    spans.pop(run)
                    grown = True
        if x1-x0 < width*.03 or abs((x0+x1)/2-middle) > width*CENTRE:
            continue
        part = still[y0:y1+1, x0:x1+1]
        density = float(part.sum())/part.size
        # Each row's still pixels and their x-extent: decide() keeps only the rows on the video's caption rows.
        counts, first, last = part.sum(axis=1), part.argmax(axis=1), part.shape[1]-1-part[:, ::-1].argmax(axis=1)
        rows = [(int(n), x0+int(a), x0+int(b)) if n else (0, -1, -1) for n, a, b in zip(counts, first, last)]
        result.append(dict(x0=x0, y0=top+y0, x1=x1, y1=top+y1, density=density, rows=rows))
    return result


def scan(path, intervals, *, check=lambda: None, progress=None):
    """Caption candidates per interval [(start_s, end_s), ...] (absolute stream times) from one decode."""
    import av
    events, owners = [], []
    for index, (start, end) in enumerate(intervals):
        start, end = max(0., start), max(0., end)
        span = max(end-start, .05)
        for point in ((.5,) if span < .8 else (.2, .5, .8)):
            moment = start+span*point
            first = max(0., min(moment, end-PAIR_S)) if span > PAIR_S else max(0., moment-PAIR_S/2)
            pair = len(owners)
            owners.append(index)
            events += [(first, 0, pair), (first+PAIR_S, 1, pair)]
    events.sort()
    found = [[] for _ in intervals]
    waiting = {}
    size = None
    with av.open(str(path)) as container:
        stream = container.streams.video[0]
        stream.thread_type = 'AUTO'
        pointer = reported = decoded = 0
        for frame in container.decode(stream):
            if pointer >= len(events):
                break
            decoded += 1
            if decoded % 30 == 0:
                check()
            if frame.time is None or frame.time+1e-3 < events[pointer][0]:
                continue
            check()
            if size is None:
                scale = min(1., ANALYSIS_WIDTH/frame.width)
                size = (max(2, round(frame.width*scale/2)*2), max(2, round(frame.height*scale/2)*2),
                        frame.width, frame.height)
            width, height = size[0], size[1]
            top = int(height*ROI_TOP)
            mask = _mask(frame.reformat(width=width, height=height, format='rgb24').to_ndarray()[top:])
            while pointer < len(events) and frame.time+1e-3 >= events[pointer][0]:
                _moment, kind, pair = events[pointer]
                if kind == 0:
                    waiting[pair] = mask
                elif pair in waiting:
                    found[owners[pair]] += _candidates(waiting.pop(pair) & ndimage.binary_dilation(mask, iterations=1),
                                                       width, height, top)
                pointer += 1
            if progress is not None and pointer-reported >= max(1, len(events)//50):
                reported = pointer
                progress(pointer/len(events))
    return found, size


def learn_band(found, size):
    """This video's caption rows and typical caption width, or None when it shows no burned-in captions."""
    if size is None:
        return None
    width, height = size[0], size[1]
    lines = [c for group in found for c in group
             if height*.015 <= c['y1']-c['y0'] <= height*.14 and .06 <= c['density'] <= .65]
    if len(lines) < max(MIN_LINES, len(found)*.1):
        return None
    centres = np.array([(c['y0']+c['y1'])/2 for c in lines])
    step = height*.006
    mode = np.bincount(np.round(centres/step).astype(int)).argmax()*step
    line = float(np.median([c['y1']-c['y0'] for c in lines]))
    near = [c for c in lines if abs((c['y0']+c['y1'])/2-mode) <= line*.6]
    if len(near) < max(MIN_LINES, len(found)*.08):
        return None
    widths = sorted(c['x1']-c['x0'] for c in near)
    return dict(y0=float(np.median([c['y0'] for c in near])), y1=float(np.median([c['y1'] for c in near])),
                line=line, typical=float(widths[min(len(widths)-1, int(len(widths)*.9))]), seen=len(near))


def _caption_parts(candidate, band):
    """The part of one candidate that is caption: its rows on the video's caption rows, plus runs of rows next to
    them that are as dense as caption rows and either stay within ROW_TOLERANCE (the caption sits a little off) or
    are tall enough to be another text line. Scenery merged above or below (a pot's rim, a pizza's crust, a hand) is
    dropped, so the box hugs the caption (22/10/2026: on six real videos the share of lines whose box reached past
    the caption rows fell from 22-48% to 0-7%)."""
    line = band['line']
    near_low, near_high = band['y0']-line*ROW_TOLERANCE, band['y1']+line*ROW_TOLERANCE
    if candidate['y1'] < near_low or candidate['y0'] > near_high:
        # A separate candidate off the caption rows counts only when shaped like a whole caption line.
        if (SECOND_LINE*line <= candidate['y1']-candidate['y0'] <= 1.5*line and candidate['density'] >= .1
                and candidate['x1']-candidate['x0'] >= band['typical']*.25):
            return [(candidate['x0'], candidate['y0'], candidate['x1'], candidate['y1'])]
        return []
    if candidate.get('rows') is None:
        return [(candidate['x0'], candidate['y0'], candidate['x1'], candidate['y1'])]
    rows = [(candidate['y0']+offset, n, a, b) for offset, (n, a, b) in enumerate(candidate['rows'])]
    core = [row for row in rows if row[1] and band['y0'] <= row[0] <= band['y1']]
    if not core:
        return []
    dense = float(np.median([row[1] for row in core]))*DENSE_ROWS
    kept, run = list(core), []
    for row in rows+[(None, 0, -1, -1)]:
        if row[0] is not None and not band['y0'] <= row[0] <= band['y1'] and row[1] and row[1] >= dense:
            run.append(row)
            continue
        if run and (len(run) >= SECOND_LINE*line or all(near_low <= r[0] <= near_high for r in run)):
            kept += run
        run = []
    return [(min(r[2] for r in kept), min(r[0] for r in kept), max(r[3] for r in kept), max(r[0] for r in kept))]


def decide(group, band, size):
    """The caption box for one line in picture pixels, and how it was found ('seen', 'typical' or None)."""
    if band is None or size is None:
        return None, None
    width, height, full_width, full_height = size
    low, high = band['y0']-band['line']*1.3, band['y1']+band['line']*1.3  # Room for a second caption line.
    kept = [c for c in group if low <= (c['y0']+c['y1'])/2 <= high and c['y1']-c['y0'] <= band['line']*3.2
            and c['density'] >= .05 and c['x1']-c['x0'] >= band['typical']*.15]
    parts = [part for c in kept for part in _caption_parts(c, band)]
    box = kind = None
    if parts:
        # Always cover the video's caption rows; reach further only for another caption line.
        box = (min(p[0] for p in parts), min(band['y0'], max(low, min(p[1] for p in parts))),
               max(p[2] for p in parts), max(band['y1'], min(high, max(p[3] for p in parts))))
        kind = 'seen' if box[2]-box[0] <= width*.95 else None
    if kind is None:
        half = band['typical']/2
        box, kind = (width/2-half, band['y0'], width/2+half, band['y1']), 'typical'
    pad = band['line']*.2
    sx, sy = full_width/width, full_height/height
    x0, y0, x1, y1 = box
    return (max(0., (x0-pad)*sx), max(0., (y0-pad)*sy), min(float(full_width), (x1+pad)*sx),
            min(float(full_height), (y1+pad)*sy)), kind


def cover_boxes(path, intervals, *, check=lambda: None, progress=None):
    """One box (x0, y0, x1, y1) or None per interval, and a short report for the render plan."""
    found, size = scan(path, intervals, check=check, progress=progress)
    band = learn_band(found, size)
    boxes, kinds = [], []
    for group in found:
        box, kind = decide(group, band, size)
        boxes.append(box)
        kinds.append(kind)
    report = dict(lines=len(intervals), seen=kinds.count('seen'), typical=kinds.count('typical'),
                  caption_rows=None if band is None else [round(band['y0']*size[3]/size[1]),
                                                          round(band['y1']*size[3]/size[1])])
    return boxes, report
