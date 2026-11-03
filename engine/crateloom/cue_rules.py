"""Conservative proposals. No automatic semantic or downbeat claims."""

def propose(beats, sections, cues, anchor_us, duration):
    """Four-slot preset; missing evidence leaves slots empty.

    Positions snap to an existing beat only within 100 ms. Existing and proposed
    cues within 250 ms are deduplicated. D uses 64 actual beat intervals and is
    only offered after the user confirms a 4/4 bar-1 anchor.
    """
    if not beats:
        return []
    occupied = {cue['slot'] for cue in cues}
    positions = [cue['position_us'] / 1_000_000 for cue in cues]
    result = []
    def add(slot, seconds, label, reason):
        if slot in occupied or not 0 <= seconds < duration: return
        if any(abs(seconds - existing) < .25 for existing in positions): return
        result.append({'slot':slot, 'position_us':round(seconds * 1_000_000), 'label':label, 'reason':reason})
        occupied.add(slot); positions.append(seconds)
    def nearest(seconds):
        index = min(range(len(beats)), key=lambda i: abs(beats[i]-seconds))
        return index if abs(round(beats[index]*1_000_000)-round(seconds*1_000_000)) <= 100_000 else None
    add(0, beats[0], 'First detected beat', 'First detected beat; review its suitability')
    chorus = next((s for s in sections if s['label'].lower() == 'chorus'), None)
    if chorus:
        index = nearest(chorus['start_us'] / 1_000_000)
        if index is not None:
            add(2, beats[index], 'Chorus', 'Within 100 ms of the chorus section')
            if anchor_us is not None:
                anchor = nearest(anchor_us / 1_000_000)
                before = index - 64
                if anchor is not None and abs(round(beats[anchor]*1_000_000)-anchor_us)<=1000 and before >= anchor and (index-anchor) % 4 == 0:
                    add(3, beats[before], '16 bars before chorus', '64 detected beats before the chorus section in confirmed 4/4')
    outro = next((s for s in sections if s['label'].lower() == 'outro'), None)
    if outro:
        index = nearest(outro['start_us'] / 1_000_000)
        if index is not None:
            add(6, beats[index], 'Outro', 'Within 100 ms of the outro section')
    return result

def auto_cues(beats, sections, cues, duration):
    """Fill free slots from evidence: first beat and labeled section boundaries."""
    occupied={c['slot'] for c in cues}
    positions=[c['position_us'] for c in cues]
    candidates=[]
    if beats: candidates.append((round(beats[0]*1000000),'First beat'))
    for section in sorted(sections,key=lambda s:s['start_us']):
        pos=section['start_us']
        if beats:
            nearest=min(beats,key=lambda b:abs(b*1000000-pos))
            if abs(round(nearest*1000000)-pos)<=250000: pos=round(nearest*1000000)
        candidates.append((pos,section['label']))
    result=[]
    for pos,label in candidates:
        if not 0<=pos<duration*1000000 or any(abs(pos-p)<250000 for p in positions): continue
        slot=next((i for i in range(8) if i not in occupied),None)
        if slot is None: break
        result.append({'slot':slot,'position_us':pos,'label':label})
        occupied.add(slot); positions.append(pos)
    return result
