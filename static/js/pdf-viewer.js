// Renders each .pdf-viewer container's PDF as canvas via PDF.js,
// instead of an <iframe>/<embed>, so there is no direct file URL
// the browser can offer to save. Deterrent-level protection only
// (see the conversation's caveat about screenshots always being possible).

document.addEventListener("DOMContentLoaded", function () {
  document.querySelectorAll(".pdf-viewer").forEach(function (container) {
    const url = container.dataset.pdfUrl;
    pdfjsLib.getDocument(url).promise.then(function (pdf) {
      for (let pageNum = 1; pageNum <= pdf.numPages; pageNum++) {
        pdf.getPage(pageNum).then(function (page) {
          const canvas = document.createElement("canvas");
          container.appendChild(canvas);
          const viewport = page.getViewport({ scale: 1.5 });
          canvas.width = viewport.width;
          canvas.height = viewport.height;
          page.render({ canvasContext: canvas.getContext("2d"), viewport: viewport });
        });
      }
    });
  });

  document.addEventListener("keydown", function (e) {
    const blocked = (e.ctrlKey || e.metaKey) && ["s", "p", "u"].includes(e.key.toLowerCase());
    if (blocked) e.preventDefault();
  });
});
