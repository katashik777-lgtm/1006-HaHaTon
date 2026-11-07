export type SourceLine = {text:string; kind:'same'|'added'|'removed'|'changed'};

// Compare lines rather than positions, so an inserted field does not mark
// every subsequent line as changed. Bound memory for unusually large files.
export function sourceDiff(before:string, after:string):{before:SourceLine[];after:SourceLine[]} {
 const a=before.split('\n'),b=after.split('\n');
 const left:SourceLine[]=a.map(text=>({text,kind:'same'}));
 const right:SourceLine[]=b.map(text=>({text,kind:'same'}));
 let start=0,endA=a.length,endB=b.length;
 while(start<endA&&start<endB&&a[start]===b[start])start++;
 while(endA>start&&endB>start&&a[endA-1]===b[endB-1]){endA--;endB--;}
 const mark=(fromA:number,toA:number,fromB:number,toB:number)=>{
  const paired=Math.min(toA-fromA,toB-fromB);
  for(let i=fromA;i<toA;i++)left[i].kind=i-fromA<paired?'changed':'removed';
  for(let j=fromB;j<toB;j++)right[j].kind=j-fromB<paired?'changed':'added';
 };
 const n=endA-start,m=endB-start;
 if(n*m>1000000){mark(start,endA,start,endB);return {before:left,after:right};}
 const width=m+1,dp=new Uint32Array((n+1)*width);
 for(let i=n-1;i>=0;i--)for(let j=m-1;j>=0;j--)
  dp[i*width+j]=a[start+i]===b[start+j]?1+dp[(i+1)*width+j+1]:Math.max(dp[(i+1)*width+j],dp[i*width+j+1]);
 let i=0,j=0,blockA=0,blockB=0;
 while(i<n&&j<m){
  if(a[start+i]===b[start+j]){mark(start+blockA,start+i,start+blockB,start+j);i++;j++;blockA=i;blockB=j;}
  else if(dp[(i+1)*width+j]>=dp[i*width+j+1])i++;else j++;
 }
 mark(start+blockA,endA,start+blockB,endB);
 return {before:left,after:right};
}
