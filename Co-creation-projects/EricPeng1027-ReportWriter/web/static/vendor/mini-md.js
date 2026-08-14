/* 极简 Markdown 渲染器（~120 行，零依赖，离线可用）
 *
 * 覆盖 Drafter 实际产出的语法：h1-h3、粗体、行内代码、无序/有序列表、
 * 简单表格、段落。先 HTML 转义再做转换，安全。
 * 暴露为全局函数：window.renderMarkdown(src) -> html 字符串
 */

(function () {
  function escapeHtml(s) {
    return String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;");
  }

  // 行内转换（在已转义的文本上做）
  function inline(s) {
    return s
      .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
      .replace(/`([^`]+)`/g, "<code>$1</code>")
      .replace(/\[([^\]]+)\]\(([^)]+)\)/g, "$1"); // 链接降级为纯文本
  }

  function isTableSep(line) {
    return /^\|?[\s:|-]+\|[\s:|-]+\|?$/.test(line.trim()) && line.indexOf("-") !== -1;
  }

  function splitRow(line) {
    var t = line.trim();
    if (t.charAt(0) === "|") t = t.slice(1);
    if (t.charAt(t.length - 1) === "|") t = t.slice(0, -1);
    return t.split("|").map(function (c) { return c.trim(); });
  }

  function render(src) {
    var lines = escapeHtml(src || "").split(/\r?\n/);
    var out = [];
    var i = 0;
    while (i < lines.length) {
      var line = lines[i];
      var trimmed = line.trim();

      // 空行
      if (!trimmed) { i++; continue; }

      // 标题
      var h = trimmed.match(/^(#{1,3})\s+(.*)$/);
      if (h) {
        var level = h[1].length;
        out.push("<h" + level + ">" + inline(h[2]) + "</h" + level + ">");
        i++;
        continue;
      }

      // 表格：当前行含 | 且下一行是分隔行
      if (trimmed.indexOf("|") !== -1 && i + 1 < lines.length && isTableSep(lines[i + 1])) {
        var header = splitRow(lines[i]);
        var rows = [];
        i += 2;
        while (i < lines.length && lines[i].trim().indexOf("|") !== -1 && lines[i].trim()) {
          rows.push(splitRow(lines[i]));
          i++;
        }
        var html = "<table><thead><tr>" +
          header.map(function (c) { return "<th>" + inline(c) + "</th>"; }).join("") +
          "</tr></thead><tbody>" +
          rows.map(function (r) {
            return "<tr>" + r.map(function (c) { return "<td>" + inline(c) + "</td>"; }).join("") + "</tr>";
          }).join("") +
          "</tbody></table>";
        out.push(html);
        continue;
      }

      // 无序列表
      if (/^[-*]\s+/.test(trimmed)) {
        var ul = [];
        while (i < lines.length && /^[-*]\s+/.test(lines[i].trim())) {
          ul.push("<li>" + inline(lines[i].trim().replace(/^[-*]\s+/, "")) + "</li>");
          i++;
        }
        out.push("<ul>" + ul.join("") + "</ul>");
        continue;
      }

      // 有序列表
      if (/^\d+[.、)]\s*/.test(trimmed)) {
        var ol = [];
        while (i < lines.length && /^\d+[.、)]\s*/.test(lines[i].trim())) {
          ol.push("<li>" + inline(lines[i].trim().replace(/^\d+[.、)]\s*/, "")) + "</li>");
          i++;
        }
        out.push("<ol>" + ol.join("") + "</ol>");
        continue;
      }

      // 分隔线
      if (/^---+$/.test(trimmed)) { out.push("<hr>"); i++; continue; }

      // 普通段落（合并连续非空行）
      var para = [];
      while (
        i < lines.length &&
        lines[i].trim() &&
        !/^(#{1,3})\s+/.test(lines[i].trim()) &&
        !/^[-*]\s+/.test(lines[i].trim()) &&
        !/^\d+[.、)]\s*/.test(lines[i].trim()) &&
        !(lines[i].trim().indexOf("|") !== -1 && i + 1 < lines.length && isTableSep(lines[i + 1]))
      ) {
        para.push(lines[i].trim());
        i++;
      }
      if (para.length) out.push("<p>" + inline(para.join(" ")) + "</p>");
    }
    return out.join("\n");
  }

  window.renderMarkdown = render;
})();
