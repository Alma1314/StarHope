"""Offline chemical formulas and editable structure asset export copies."""
from starhope.chemistry import chemical_latex_packages, wrap_chemical_drawing
from starhope.paper_helper import build_latex_document
from test_math_preview_regressions import run_preview_script


def test_chemical_formulas_units_conditions_and_bonds_use_bundled_mhchem():
    run_preview_script(r'''
const samples = [
 String.raw`\ce{H2O}`, String.raw`\ce{SO4^2-}`, String.raw`\ce{^{14}_{6}C}`,
 String.raw`\ce{2H2 + O2 -> 2H2O}`, String.raw`\ce{N2 + 3H2 <=> 2NH3}`,
 String.raw`\ce{CaCO3 ->[\text{加热}] CaO + CO2 ^}`,
 String.raw`\ce{Ag+ + Cl- -> AgCl v}`, String.raw`\ce{CH3-CH2-OH}`,
 String.raw`\ce{H2C=CH2}`, String.raw`\ce{HC#CH}`, String.raw`\pu{1.0 mol L-1}`
];
for (const formula of samples) {
 const html = katex.renderToString(formula,{throwOnError:true,trust:false});
 assert(!html.includes('katex-error'),formula);
 assert.deepEqual(parsedFormulas(preprocessFormulaForKaTeX('$'+formula+'$')),[formula]);
 assert.deepEqual(parsedFormulas(preprocessFormulaForKaTeX('物质 '+formula+'。')),[formula]);
}
const choices=String.raw`\begin{choices}\item \ce{H2O}\item \ce{CO2}\item \ce{O2}\item \ce{H2}\end{choices}`;
assert.equal(parsedFormulas(preprocessFormulaForKaTeX(choices)).length,4);
const table=String.raw`\begin{tabular}{cc}物质 & \ce{SO4^2-} \\\end{tabular}`;
assert(parsedFormulas(preprocessFormulaForKaTeX(table)).includes(String.raw`\ce{SO4^2-}`));
''')


def test_chemical_source_does_not_rewrite_code_urls_or_incomplete_commands():
    run_preview_script(r'''
for(const source of [String.raw`\ce{H2O`,String.raw`\centering`,String.raw`\\ce{H2O}`,
 '`\\ce{H2O}`',String.raw`https://example.org/\ce{H2O}`,String.raw`![\ce{H2O}](/static/uploads/water.png)`]) {
 assert.equal(normalizeNakedMathForPreview(source),source);
}
const source=String.raw`\ce{CaCO3 ->[\text{加热}] CaO + CO2 ^}`;
assert.equal(parsedFormulas(preprocessFormulaForKaTeX(source)).length,1);
''')


def test_chemical_structures_use_existing_editable_tikz_assets_and_pdf_preamble():
    chemical=r'\chemfig{*6(=-=-=-)}'
    wrapped=wrap_chemical_drawing(chemical)
    assert chemical in wrapped
    assert wrapped.startswith(r'\begin{tikzpicture}')
    assert wrap_chemical_drawing(wrapped)==wrapped
    assert chemical+' % comment\n};' in wrap_chemical_drawing(chemical+' % comment')
    assert chemical_latex_packages(wrapped)==r'\usepackage{chemfig}'
    assert chemical_latex_packages(r'\ce{H2O}')==r'\usepackage{mhchem}'
    assert chemical_latex_packages(r'\pu{1 mol L-1}')==''
    assert chemical_latex_packages(r'\centering \chemfigure{x}')==''
    image='/static/uploads/tikz_chemical.png'
    question={'id':1,'content':'合成结构题\n![]('+image+')','question_type':'detailed_answer',
              'content_tikz_assets':[{'id':'chemical','image_path':image,'tikz_code':wrapped}]}
    tex=build_latex_document('化学试卷','','exam',[{'question':question,'score':5}])
    assert tex.index(r'\usepackage{chemfig}') < tex.index(r'\begin{document}')
    assert wrapped in tex and 'tikz_chemical.png' not in tex
    assert question['content_tikz_assets'][0]['tikz_code']==wrapped


def test_pdf_compilation_reports_missing_bundled_template_without_running_tex(tmp_path, monkeypatch):
    from starhope import paper_helper
    monkeypatch.setattr(paper_helper, 'TEMPLATES_DIR', tmp_path / 'missing-templates')
    pdf, message = paper_helper.compile_tex_to_pdf(r"\documentclass{article}\begin{document}missing template check\end{document}")
    assert pdf is None and '模板读取失败' in message
