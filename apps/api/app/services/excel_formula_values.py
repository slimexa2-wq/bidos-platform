"""读取简单数值公式，并在保存时保留公式缓存；不执行Excel代码/宏。"""
import ast
import html
import operator
import re
import zipfile
import xml.etree.ElementTree as ET
from decimal import Decimal
from openpyxl.utils.cell import range_boundaries, get_column_letter
from openpyxl.formula.translate import Translator

NS = {'m': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main', 'r': 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'}
OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv, ast.Pow: operator.pow}

class FormulaValues:
    def __init__(self, path):
        self.cells, self.paths, self.formulas, self.memo = {}, {}, {}, {}
        with zipfile.ZipFile(path) as z:
            rels = {x.attrib['Id']: x.attrib['Target'] for x in ET.fromstring(z.read('xl/_rels/workbook.xml.rels'))}
            for sheet in ET.fromstring(z.read('xl/workbook.xml')).find('m:sheets', NS):
                target = rels[sheet.attrib['{'+NS['r']+'}id']]
                filename = target.lstrip('/') if target.startswith('/') else 'xl/' + target
                self.paths[sheet.attrib['name']] = filename
                data = ET.fromstring(z.read(filename))
                shared = {}
                for c in data.findall('.//m:sheetData/m:row/m:c', NS):
                    key = (sheet.attrib['name'], c.attrib['r']); self.cells[key] = c
                    f = c.find('m:f', NS)
                    if f is not None:
                        if f.text:
                            self.formulas[key] = f.text
                            if f.attrib.get('t') == 'shared': shared[f.attrib.get('si')] = (key[1], f.text)
                        elif f.attrib.get('t') == 'shared' and f.attrib.get('si') in shared:
                            origin, expression = shared[f.attrib['si']]
                            self.formulas[key] = Translator('='+expression, origin=origin).translate_formula(key[1])[1:]

    def number(self, key, seen=None):
        if key in self.memo: return self.memo[key]
        seen = set(seen or ())
        if key in seen: raise ValueError('循环引用')
        seen.add(key); cell = self.cells.get(key)
        if cell is None: return Decimal(0)
        if key not in self.formulas:
            value = cell.find('m:v', NS)
            if value is None or not value.text:
                if cell.find('m:is', NS) is not None: raise ValueError('文本')
                return Decimal(0)
            if cell.attrib.get('t') in ('s','str','inlineStr','e'): raise ValueError('非数值')
            return Decimal(value.text)
        formula = self.formulas[key]
        if re.fullmatch(r'SUM\([A-Z]+\d+:[A-Z]+\d+\)', formula, re.I):
            bounds = range_boundaries(formula[4:-1]); total = Decimal(0)
            for row in range(bounds[1],bounds[3]+1):
                for col in range(bounds[0],bounds[2]+1):
                    try: total += self.number((key[0],get_column_letter(col)+str(row)),seen)
                    except ValueError: pass  # Excel SUM忽略文本。
            self.memo[key] = total; return total
        formula = re.sub(r'\$?([A-Z]{1,3})\$?(\d+)', lambda m: str(self.number((key[0],m[1]+m[2]),seen)),formula)
        formula = re.sub(r'(\d+(?:\.\d+)?)%',r'(\1/100)',formula).replace('^','**')
        def walk(node):
            if isinstance(node,ast.Constant) and type(node.value) in (int,float):return Decimal(str(node.value))
            if isinstance(node,ast.UnaryOp) and isinstance(node.op,(ast.UAdd,ast.USub)):return walk(node.operand)*(1 if isinstance(node.op,ast.UAdd) else -1)
            if isinstance(node,ast.BinOp) and type(node.op) in OPS:
                a,b=walk(node.left),walk(node.right)
                if isinstance(node.op,ast.Pow) and abs(b)>20:raise ValueError('指数过大')
                return OPS[type(node.op)](a,b)
            raise ValueError('无法读取该公式')
        result=walk(ast.parse(formula,mode='eval').body)
        if not result.is_finite():raise ValueError('非有限值')
        self.memo[key]=result;return result

    def missing(self):
        values, warnings = {}, {}
        for key in self.formulas:
            cached=self.cells[key].find('m:v',NS)
            if cached is not None and cached.text:continue
            try: values[key]=float(self.number(key))
            except (ValueError,ArithmeticError,SyntaxError):warnings[key]='原公式没有缓存且无法按明确数值计算'
        return values,warnings


def preserve_formula_caches(original, prepared):
    old,new=FormulaValues(original),FormulaValues(prepared)
    replacements={}
    for key,formula in new.formulas.items():
        value=None; value_type=''
        try:value=str(new.number(key))
        except (ValueError,ArithmeticError,SyntaxError):
            if old.formulas.get(key)==formula:
                cached=old.cells[key].find('m:v',NS)
                if cached is not None:
                    value=cached.text; value_type=old.cells[key].attrib.get('t','')
        if value is not None:replacements.setdefault(new.paths[key[0]],{})[key[1]]=(value,value_type)
    with zipfile.ZipFile(prepared) as z:entries=[(info,z.read(info.filename)) for info in z.infolist()]
    with zipfile.ZipFile(prepared,'w') as z:
        for info,body in entries:
            xml = body.decode('utf-8') if info.filename.startswith('xl/worksheets/') and info.filename.endswith('.xml') or info.filename == 'xl/sharedStrings.xml' else None
            if info.filename in replacements:
                if xml is None: xml=body.decode('utf-8')
                for coord,(value,value_type) in replacements[info.filename].items():
                    pattern=r'(<c\b[^>]*\br="'+re.escape(coord)+r'"[^>]*>)(.*?)(</c>)'
                    def patch(m):
                        inner=re.sub(r'<v(?:\s[^>]*)?>.*?</v>|<v\s*/>','',m[2],flags=re.S)
                        start=re.sub(r'\s+t="[^"]*"','',m[1])
                        if value_type: start=start[:-1]+' t="'+value_type+'">'
                        return start+inner+'<v>'+value+'</v>'+m[3]
                    xml=re.sub(pattern,patch,xml,flags=re.S)
            if xml is not None:
                # 富文本中单独一个换行/空格的文字段也必须保留；原库只给非空白文字加此标记。
                # 只补XML空白保留属性，不改变任何输入文字。
                def preserve_whitespace(m):
                    value=html.unescape(m[2])
                    start=m[1]
                    if value and value.strip()!=value and 'xml:space=' not in start:
                        start=start[:-1]+' xml:space="preserve">'
                    return start+m[2]+m[3]
                xml=re.sub(r'(<(?:[A-Za-z_][\w.-]*:)?t\b[^>]*>)([^<]*)(</(?:[A-Za-z_][\w.-]*:)?t>)',preserve_whitespace,xml)
                body=xml.encode('utf-8')
            z.writestr(info,body)
