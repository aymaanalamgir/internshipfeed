"""Tiny deterministic text/blank PDFs; no external fixture or generator dependency."""
from typing import Optional, Sequence


def make_pdf(pages: Sequence[Optional[str]]) -> bytes:
    objects = [
        b'<< /Type /Catalog /Pages 2 0 R >>',
        ('<< /Type /Pages /Kids [%s] /Count %s >>' %
         (' '.join('%s 0 R' % (4 + 2 * i) for i in range(len(pages))), len(pages))).encode(),
        b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',
    ]
    for index, text in enumerate(pages):
        commands = ['BT /F1 12 Tf 50 740 Td 16 TL']
        for line in (text or '').splitlines():
            escaped = line.replace('\\', '\\\\').replace('(', '\\(').replace(')', '\\)')
            commands.append('(%s) Tj T*' % escaped)
        commands.append('ET')
        content = '\n'.join(commands).encode('latin-1')
        objects.append(('<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] '
                        '/Resources << /Font << /F1 3 0 R >> >> /Contents %s 0 R >>' % (5 + 2 * index)).encode())
        objects.append(('<< /Length %s >>\nstream\n' % len(content)).encode() + content + b'\nendstream')
    output = bytearray(b'%PDF-1.4\n')
    offsets = []
    for number, obj in enumerate(objects, start=1):
        offsets.append(len(output))
        output.extend(('%s 0 obj\n' % number).encode() + obj + b'\nendobj\n')
    xref = len(output)
    output.extend(('xref\n0 %s\n0000000000 65535 f \n' % (len(objects) + 1)).encode())
    for offset in offsets:
        output.extend(('%010d 00000 n \n' % offset).encode())
    output.extend(('trailer\n<< /Size %s /Root 1 0 R >>\nstartxref\n%s\n%%%%EOF\n' %
                   (len(objects) + 1, xref)).encode())
    return bytes(output)
