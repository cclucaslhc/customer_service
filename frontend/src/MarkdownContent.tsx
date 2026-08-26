import type { ComponentPropsWithoutRef } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import "./markdown.css";

type MarkdownContentProps = {
  children: string;
  className?: string;
};

function isExternalLink(href?: string): boolean {
  return Boolean(href && /^(?:https?:)?\/\//i.test(href));
}

const components: Components = {
  table: ({ node: _node, children, ...props }) => (
    <div
      className="markdown-table-scroll"
      role="region"
      aria-label="可横向滚动的表格"
      tabIndex={0}
    >
      <table {...props}>{children}</table>
    </div>
  ),
  a: ({ node: _node, href, children, ...props }) => {
    const external = isExternalLink(href);
    return (
      <a
        {...props}
        href={href}
        target={external ? "_blank" : undefined}
        rel={external ? "noreferrer noopener" : undefined}
      >
        {children}
      </a>
    );
  },
  img: ({ node: _node, alt = "", ...props }) => (
    <img
      {...(props as ComponentPropsWithoutRef<"img">)}
      alt={alt}
      loading="lazy"
      decoding="async"
      referrerPolicy="no-referrer"
    />
  ),
};

export default function MarkdownContent({ children, className = "" }: MarkdownContentProps) {
  return (
    <div className={`markdown-content ${className}`.trim()}>
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
        {children}
      </ReactMarkdown>
    </div>
  );
}
