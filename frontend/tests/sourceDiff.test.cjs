const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const ts=require('typescript');
const compiled=ts.transpileModule(fs.readFileSync(require('node:path').join(__dirname,'../src/sourceDiff.ts'),'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS}}).outputText;
const scope={exports:{}};
new Function('exports',compiled)(scope.exports);
const {sourceDiff}=scope.exports;
const kinds=lines=>lines.map(line=>line.kind);

test('identical documents have no highlights',()=>{
 const result=sourceDiff('{\n  "value": 1\n}','{\n  "value": 1\n}');
 assert.deepEqual(kinds(result.before),['same','same','same']);
 assert.deepEqual(result.before,result.after);
});
test('changed value is highlighted in both documents',()=>{
 const result=sourceDiff('{\n  "value": 1\n}','{\n  "value": 2\n}');
 assert.deepEqual(kinds(result.before),['same','changed','same']);
 assert.deepEqual(kinds(result.after),['same','changed','same']);
});
test('insertions and deletions do not shift subsequent highlights',()=>{
 const result=sourceDiff('start\nremoved\nanchor\nend','start\nanchor\nadded\nend');
 assert.deepEqual(kinds(result.before),['same','removed','same','same']);
 assert.deepEqual(kinds(result.after),['same','same','added','same']);
});
test('source text is preserved, including blank lines and markup',()=>{
 const before='\n<script>one</script>\n',after='\n<script>two</script>\n';
 const result=sourceDiff(before,after);
 assert.equal(result.before.map(x=>x.text).join('\n'),before);
 assert.equal(result.after.map(x=>x.text).join('\n'),after);
});
test('large changed blocks use bounded comparison',()=>{
 const result=sourceDiff(Array(2000).fill('before').join('\n'),Array(2000).fill('after').join('\n'));
 assert.equal(result.before.length,2000);
 assert.ok(result.after.every(x=>x.kind==='changed'));
});
