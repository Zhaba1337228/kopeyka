"""Locate table rules in the actual image; no page fractions or form coordinates."""
import numpy as np


def bands(positions, gap=2):
    groups=[]
    for position in positions:
        value=int(position)
        if groups and value<=groups[-1][-1]+gap:
            groups[-1].append(value)
        else:
            groups.append([value])
    return [(g[0],g[-1]) for g in groups]


def horizontal_rules(image):
    dark=np.asarray(image.convert('L'))<170
    counts=dark.sum(axis=1)
    if counts.max(initial=0)<image.width*.25:
        return []
    return bands(np.flatnonzero(counts>=max(image.width*.25,counts.max()*.65)))


def vertical_rules(image, top, bottom):
    pixels=np.asarray(image.convert('L'))[round(top):round(bottom)]<170
    if not len(pixels):
        return []
    return [(a+b)/2 for a,b in bands(np.flatnonzero(pixels.mean(axis=0)>.8))]


def body_top(rules):
    gaps=[b[0]-a[1] for a,b in zip(rules,rules[1:])]
    threshold=max(20,float(np.median(gaps))*2)
    index=len(rules)-2
    while index>0 and gaps[index-1]<=threshold:
        index-=1
    return rules[index][0]


def vertical_start(image,x,top,bottom):
    """Top of the continuous border ending at a body cell (merged-header depth)."""
    pixels=np.asarray(image.convert('L'))[round(top):round(bottom),max(0,round(x)-3):round(x)+4]<170
    if not pixels.size:
        return bottom
    occupied=np.flatnonzero(pixels.any(axis=1))
    runs=bands(occupied,gap=8)
    return top+runs[-1][0] if runs else bottom
