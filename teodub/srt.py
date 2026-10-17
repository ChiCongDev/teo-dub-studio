from pathlib import Path


def stamp(ms):
    hours, ms = divmod(int(ms), 3600000)
    minutes, ms = divmod(ms, 60000)
    seconds, ms = divmod(ms, 1000)
    return f'{hours:02}:{minutes:02}:{seconds:02},{ms:03}'


def write_srt(path, cues):
    with Path(path).open('x', encoding='utf-8') as file:
        file.write('\n\n'.join(f'{i}\n{stamp(c["start_ms"])} --> {stamp(c["end_ms"])}\n{c["text"]}'
                               for i, c in enumerate(cues, 1)) + '\n')
