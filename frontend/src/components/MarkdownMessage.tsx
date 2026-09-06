import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import ChartBlock from "./ChartBlock";

type Props = {
  content: string;
};

function childClassName(children: unknown): string {
  const child = Array.isArray(children) ? children[0] : children;
  if (child && typeof child === "object" && "props" in child) {
    return (child as { props?: { className?: string } }).props?.className ?? "";
  }
  return "";
}

function textContent(children: unknown): string {
  if (typeof children === "string") return children;
  if (Array.isArray(children)) return children.map(textContent).join("");
  if (children && typeof children === "object" && "props" in children) {
    return textContent((children as { props?: { children?: unknown } }).props?.children);
  }
  return "";
}

/**
 * Renders an assistant reply's Markdown (bold, lists, tables, code, links -
 * see remark-gfm) as real elements instead of printing the raw
 * `**`/`-`/`|` syntax as plain text. Only used for assistant messages -
 * user messages stay plain text (see AppShellPage.tsx), matching the
 * ChatGPT/Claude convention of only rendering the model's own output as
 * rich text. react-markdown never uses dangerouslySetInnerHTML, so this
 * is safe even though the content is model-generated.
 */
export default function MarkdownMessage({ content }: Props) {
  return (
    <div className="prose-message">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          p: ({ children }) => <p className="mb-2 last:mb-0">{children}</p>,
          strong: ({ children }) => <strong className="font-semibold">{children}</strong>,
          em: ({ children }) => <em className="italic">{children}</em>,
          ul: ({ children }) => <ul className="mb-2 list-disc space-y-1 pl-5 last:mb-0">{children}</ul>,
          ol: ({ children }) => <ol className="mb-2 list-decimal space-y-1 pl-5 last:mb-0">{children}</ol>,
          li: ({ children }) => <li>{children}</li>,
          h1: ({ children }) => <h1 className="mb-2 mt-3 text-base font-semibold first:mt-0">{children}</h1>,
          h2: ({ children }) => <h2 className="mb-2 mt-3 text-base font-semibold first:mt-0">{children}</h2>,
          h3: ({ children }) => <h3 className="mb-1.5 mt-2 text-sm font-semibold first:mt-0">{children}</h3>,
          a: ({ children, href }) => (
            <a
              href={href}
              target="_blank"
              rel="noopener noreferrer"
              className="text-brand-600 underline hover:text-brand-700 dark:text-brand-400 dark:hover:text-brand-300"
            >
              {children}
            </a>
          ),
          code: ({ children, className }) => {
            if (/language-chart/.test(className ?? "")) {
              return <ChartBlock raw={textContent(children)} />;
            }
            const isBlock = /language-/.test(className ?? "");
            return isBlock ? (
              <code className={className}>{children}</code>
            ) : (
              <code className="rounded bg-slate-100 px-1 py-0.5 text-[0.85em] dark:bg-slate-800">
                {children}
              </code>
            );
          },
          pre: ({ children }) => {
            // A ```chart block's <code> renders its own bordered container
            // (see ChartBlock) - don't additionally wrap it in <pre>'s
            // monospace/code-block styling.
            if (/language-chart/.test(childClassName(children))) {
              return <>{children}</>;
            }
            return (
              <pre className="mb-2 overflow-x-auto rounded-lg bg-slate-100 p-3 text-xs last:mb-0 dark:bg-slate-800">
                {children}
              </pre>
            );
          },
          blockquote: ({ children }) => (
            <blockquote className="mb-2 border-l-2 border-slate-300 pl-3 text-slate-600 last:mb-0 dark:border-slate-600 dark:text-slate-400">
              {children}
            </blockquote>
          ),
          hr: () => <hr className="my-3 border-slate-200 dark:border-slate-700" />,
          table: ({ children }) => (
            <div className="mb-2 overflow-x-auto last:mb-0">
              <table className="border-collapse text-left text-xs">{children}</table>
            </div>
          ),
          thead: ({ children }) => <thead className="border-b border-slate-300 dark:border-slate-600">{children}</thead>,
          th: ({ children }) => <th className="px-2 py-1 font-semibold">{children}</th>,
          td: ({ children }) => (
            <td className="border-t border-slate-200 px-2 py-1 dark:border-slate-700">{children}</td>
          ),
        }}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
}
