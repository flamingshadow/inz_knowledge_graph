// Export a snapshot of the public INZ Operational Manual from a browser.
//
// Use this where the machine running the loader cannot reach immigration.govt.nz
// directly (the manual's chapters load through a script, so a browser always works).
//
// 1. Open https://www.immigration.govt.nz/opsmanual/ in a browser.
// 2. Open the developer console and paste this whole file.
// 3. A file named opsmanual-YYYY-MM-DD.json.gz downloads. Put it in data/snapshots/
//    and run:  inzkg load --snapshot data/snapshots/opsmanual-YYYY-MM-DD.json.gz
//
// The snapshot holds the manual's contents file (every instruction with its dated
// earlier versions) and the text of every page except the Refugees and Protection
// chapter, which is out of scope.
(async () => {
  const OUT_OF_SCOPE = new Set(['Refugees and protection']);
  const toc = await fetch('toc.json?node=NaN').then(r => r.json());
  const search = await fetch('search.json').then(r => r.json());
  const excluded = new Set();
  const walk = (nodes, top) => {
    for (const n of nodes) {
      const chapter = top ?? n.text;
      if (OUT_OF_SCOPE.has(chapter)) excluded.add(n.url);
      if (n.children) walk(n.children, chapter);
    }
  };
  walk(toc, null);
  const pages = search.pages.filter(p => !excluded.has(p.url));
  const bundle = JSON.stringify({
    retrieved: new Date().toISOString(),
    source: location.origin + '/opsmanual/',
    issueDate: (document.title.match(/Issue Date: ([^O]+?\d{4})/) || [])[1] || null,
    toc, pages,
  });
  const gz = await new Response(new Blob([bundle]).stream().pipeThrough(new CompressionStream('gzip'))).blob();
  const a = document.createElement('a');
  a.href = URL.createObjectURL(gz);
  a.download = `opsmanual-${new Date().toISOString().slice(0, 10)}.json.gz`;
  a.click();
  console.log(`Exported ${pages.length} pages and the contents file.`);
})();
