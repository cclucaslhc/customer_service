import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import MarkdownContent from "./MarkdownContent";

const render = (markdown: string) => renderToStaticMarkup(
  <MarkdownContent>{markdown}</MarkdownContent>,
);

describe("MarkdownContent", () => {
  it("renders GFM tables, task lists and strikethrough content", () => {
    const html = render([
      "| 缺陷类型 | 处理方式 |",
      "| --- | --- |",
      "| 引脚氧化 | 隔离复检 |",
      "",
      "- [x] 已检查",
      "- [ ] 待处理",
      "",
      "~~旧标准~~",
    ].join("\n"));

    expect(html).toContain("markdown-table-scroll");
    expect(html).toContain("<table>");
    expect(html).toContain("<th>缺陷类型</th>");
    expect(html).toContain('type="checkbox"');
    expect(html).toContain("<del>旧标准</del>");
  });

  it("renders images, safe links, blockquotes and code without executing raw HTML", () => {
    const html = render([
      "![缺陷示意图](https://example.com/defect.png \"缺陷图\")",
      "",
      "[查看说明](https://example.com/guide)",
      "",
      "> 处理提示",
      "",
      "```json",
      '{"status":"ok"}',
      "```",
      "",
      '<img src=x onerror="alert(1)">',
    ].join("\n"));

    expect(html).toContain('src="https://example.com/defect.png"');
    expect(html).toContain('loading="lazy"');
    expect(html).toContain('referrerPolicy="no-referrer"');
    expect(html).toContain('target="_blank"');
    expect(html).toContain('rel="noreferrer noopener"');
    expect(html).toContain("<blockquote>");
    expect(html).toContain('class="language-json"');
    expect(html).toContain('&lt;img src=x onerror=&quot;alert(1)&quot;&gt;');
    expect(html).not.toContain('<img src="x"');
  });
});
