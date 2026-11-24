let failures = 0;
const fs = require('node:fs');
const path = require('node:path');
const ts = require(require.resolve('typescript', {paths: [process.cwd()]}));

function collectTsxFiles(dir) {
  const results = [];
  if (!fs.existsSync(dir)) return results;
  for (const entry of fs.readdirSync(dir, {withFileTypes: true})) {
    const full = path.posix.join(dir, entry.name);
    if (entry.isDirectory()) results.push(...collectTsxFiles(full));
    else if (entry.name.endsWith('.tsx')) results.push(full);
  }
  return results;
}

const files = [
  'App.tsx',
  ...collectTsxFiles('components'),
  ...collectTsxFiles('app'),
];

function isEmptyLiteralAttribute(attr) {
  if (!attr.initializer) return true;
  if (ts.isStringLiteral(attr.initializer)) {
    return attr.initializer.text.trim().length === 0;
  }
  if (ts.isJsxExpression(attr.initializer) && attr.initializer.expression) {
    const expr = attr.initializer.expression;
    if (ts.isStringLiteral(expr) || ts.isNoSubstitutionTemplateLiteral(expr)) {
      return expr.text.trim().length === 0;
    }
  }
  return false;
}

for (const filename of files) {
  const code = fs.readFileSync(filename, 'utf8');
  const source = ts.createSourceFile(filename, code, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  function walk(node) {
    if (
      ts.isJsxText(node) &&
      ts.isJsxElement(node.parent) &&
      ['View', 'Pressable', 'ScrollView', 'SafeAreaView', 'KeyboardAvoidingView'].includes(
        node.parent.openingElement.tagName.getText(source),
      )
    ) {
      const raw = node.getText(source);
      if (raw.trim() || (!/[\r\n]/.test(raw) && raw.length)) {
        failures++;
        console.log(
          filename +
            ':' +
            (source.getLineAndCharacterOfPosition(node.getStart()).line + 1) +
            ' Raw text inside a native layout container: ' +
            JSON.stringify(raw),
        );
      }
    }
    if (ts.isJsxOpeningElement(node) || ts.isJsxSelfClosingElement(node)) {
      const name = node.tagName.getText(source);
      const attrs = node.attributes.properties.filter(ts.isJsxAttribute);
      const props = attrs.map(p => p.name.text);
      const labelAttr = attrs.find(p => p.name.text === 'accessibilityLabel');
      if (labelAttr && isEmptyLiteralAttribute(labelAttr)) {
        failures++;
        console.log(
          'EMPTY ACCESSIBILITY LABEL ' +
            filename +
            ':' +
            (source.getLineAndCharacterOfPosition(labelAttr.getStart()).line + 1),
        );
      }
      if (name === 'TextInput' && !props.includes('accessibilityLabel') && !props.includes('aria-labelledby')) {
        failures++;
        console.log(
          filename +
            ':' +
            (source.getLineAndCharacterOfPosition(node.getStart()).line + 1) +
            ' ' +
            node.getText(source).slice(0, 160),
        );
      }
      if (name === 'Pressable' && props.filter(p => p === 'accessibilityRole').length > 1) {
        failures++;
        console.log(
          'DUPLICATE ROLE ' + filename + ':' + (source.getLineAndCharacterOfPosition(node.getStart()).line + 1),
        );
      }
    }
    ts.forEachChild(node, walk);
  }
  walk(source);
}

if (failures) throw new Error(`${failures} accessibility or native text-container violations.`);
console.log(
  `All editable inputs across ${files.length} screens/components have non-empty accessible names; actions have unique roles; native layout containers have no literal text.`,
);
