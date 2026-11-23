"""Shared chemistry package selection for local drawing and export copies."""
import re


def chemical_latex_packages(source: str) -> str:
    packages = []
    if re.search(r"\\(?:chemfig|schemestart)(?![A-Za-z])", source):
        packages.append("chemfig")
    # KaTeX's \pu extension is not a command in the TeX mhchem package.
    if re.search(r"\\ce(?![A-Za-z])", source):
        packages.append("mhchem")
    return "\\usepackage{" + ",".join(packages) + "}" if packages else ""


def wrap_chemical_drawing(source: str) -> str:
    """Store chemistry in the existing editable TikZ asset format."""
    if (re.search(r"\\chemfig(?![A-Za-z])", source)
            and not re.search(r"\\begin\s*\{\s*tikzpicture\s*\}", source)):
        return "\\begin{tikzpicture}\n\\node[inner sep=0pt] {\n" + source.strip() + "\n};\n\\end{tikzpicture}"
    return source
