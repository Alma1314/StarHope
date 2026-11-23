"""Opt-in real XeLaTeX and Word exports with synthetic chemistry only."""
import io
import os
import zipfile

import pytest
from PIL import Image
import pymupdf

from starhope import word_export_helper as word
from starhope.paper_helper import build_latex_document, compile_tex_to_pdf
from starhope.chemistry import wrap_chemical_drawing

pytestmark=pytest.mark.skipif(os.getenv('STARHOPE_TEST_CHEMISTRY_NATIVE')!='1',reason='requires local XeLaTeX chemistry packages')


def test_native_chemistry_png_and_word_pdf_exports(tmp_path):
    formulas=[r'\ce{SO4^2-}',r'\ce{CaCO3 ->[\text{heat}] CaO + CO2 ^}',r'\ce{2H2 + O2 -> 2H2O}']
    structures=[r'\chemfig{*6(=-=-=-)}',r'\chemfig{CH_3-CH_2-OH}',
                r'\chemfig{H_3C-C(-[:90]OH)(<:[:30]H)>[:-30]CH_2-CH_3}']
    for index,formula in enumerate(formulas+structures):
        png=word._formula_fallback_png(formula)
        assert png,formula
        with Image.open(io.BytesIO(png)) as image:
            assert image.width>10 and image.height>10
        (tmp_path/f'formula-{index}.png').write_bytes(png)
    asset={'id':'chemical','image_path':'/static/uploads/formula-3.png',
           'tikz_code':wrap_chemical_drawing(structures[0])}
    content='Chemical reaction $'+formulas[1]+'$\n\n![](/static/uploads/formula-3.png)'
    question={'id':1,'content':content,'question_type':'detailed_answer','difficulty':'medium',
              'image_paths':['/static/uploads/formula-3.png'],'content_tikz_assets':[asset],
              'answer_markdown':'Sulfate: $'+formulas[0]+'$'}
    questions=[{'question':question,'score':5}]
    docx,diagnostic=word.build_word_document('Chemistry export','','exam',questions,uploads_dir=tmp_path,
                                           include_answers=True,show_secret=False,show_notice=False)
    assert diagnostic['failed_formulas']==0,diagnostic
    assert diagnostic['missing_images']==0,diagnostic
    (tmp_path/'chemistry.docx').write_bytes(docx)
    with zipfile.ZipFile(io.BytesIO(docx)) as archive:
        assert any(archive.read(name)==(tmp_path/'formula-3.png').read_bytes() for name in archive.namelist() if name.startswith('word/media/'))
    tex=build_latex_document('Chemistry export','','exam',questions,include_answers=True,show_secret=False,show_notice=False)
    assert asset['tikz_code'] in tex
    (tmp_path/'chemistry.tex').write_text(tex,encoding='utf-8')
    pdf,diagnostic=compile_tex_to_pdf(tex)
    assert pdf,diagnostic
    (tmp_path/'chemistry.pdf').write_bytes(pdf)
    with pymupdf.open(stream=pdf,filetype='pdf') as document:
        assert len(document)>=1
        assert 'Chemical reaction' in ''.join(page.get_text() for page in document)
        assert sum(len(page.get_drawings()) for page in document)>6
        pixmap=document[0].get_pixmap(matrix=pymupdf.Matrix(1.3,1.3))
        pixmap.save(tmp_path/'chemistry-pdf.png')
