import { Globe, ChevronDown } from "lucide-react";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { useScrollAnchor } from "./use-scroll-anchor";

export function SearchStepGroup({
  title = "Searched the web",
  children,
  defaultOpen = false
}: {
  title?: string;
  children: React.ReactNode;
  defaultOpen?: boolean;
}) {
  const anchorOnOpenChange = useScrollAnchor();

  return (
    <Collapsible defaultOpen={defaultOpen} className="group/step w-full" onOpenChange={anchorOnOpenChange}>
      <CollapsibleTrigger className="flex w-full items-center gap-2 py-1.5 text-[13px] text-muted-foreground/80 hover:text-foreground transition-colors outline-none cursor-pointer">
        <span className="text-left capitalize">{title}</span>
        <ChevronDown className="size-3.5 text-muted-foreground/50 transition-transform group-data-[open]/step:rotate-180" />
      </CollapsibleTrigger>
      <CollapsibleContent className="pb-2">
        <div className="flex flex-col gap-4 mt-2">
          {children}
        </div>
      </CollapsibleContent>
    </Collapsible>
  );
}

export function SearchToolCall({ 
  query, 
  results, 
  resultString,
  defaultOpen = false,
}: { 
  query: string; 
  results?: Array<{ title?: string; url?: string; snippet?: string }>; 
  resultString?: string;
  defaultOpen?: boolean;
}) {
  // Fallback parsing if results is undefined but resultString is valid JSON
  let parsedResults = results;
  if (!parsedResults && resultString) {
    try {
      const parsed = JSON.parse(resultString);
      if (Array.isArray(parsed)) parsedResults = parsed;
      else if (parsed.results && Array.isArray(parsed.results)) parsedResults = parsed.results;
      else if (parsed.organic_results && Array.isArray(parsed.organic_results)) parsedResults = parsed.organic_results;
    } catch {
      // Ignore
    }
  }

  return (
    <Collapsible defaultOpen={defaultOpen} className="group/search w-full">
      <CollapsibleTrigger className="flex w-full items-center gap-2 py-1.5 px-2 -mx-2 rounded-md hover:bg-muted/40 focus:outline-none transition-colors cursor-pointer">
        <div className="flex items-center justify-center w-[24px]">
          <Globe className="size-3.5 text-muted-foreground shrink-0" />
        </div>
        <span className="flex-1 text-[13px] text-muted-foreground/80 font-medium truncate text-left group-hover/search:text-foreground transition-colors">
          {query}
        </span>
        {parsedResults && (
          <span className="text-[11px] text-muted-foreground/50 tabular-nums shrink-0">
            {parsedResults.length} results
          </span>
        )}
      </CollapsibleTrigger>
      <CollapsibleContent>
        <div className="pl-[24px] pr-2 pt-1 pb-2 w-full">
          <div className="flex flex-col border border-border/60 rounded-md overflow-hidden bg-background w-full shadow-sm">
            {parsedResults && parsedResults.length > 0 ? (
              parsedResults.slice(0, 5).map((res, i) => (
                 <div key={i} className="flex items-center gap-3 px-3 py-2 border-b border-border/40 last:border-b-0 hover:bg-muted/30 transition-colors max-w-full">
                   {/* Favicon placeholder */}
                   <div className="flex-shrink-0 w-3.5 h-3.5 rounded-sm flex items-center justify-center text-[10px] font-bold" 
                        style={{ 
                          backgroundColor: `hsl(${(i * 50) % 360}, 70%, 90%)`,
                          color: `hsl(${(i * 50) % 360}, 70%, 30%)`
                        }}>
                      {res.title ? res.title.charAt(0).toUpperCase() : "W"}
                   </div>
                   <a href={res.url} target="_blank" rel="noreferrer" className="flex-1 truncate text-[12px] text-foreground/90 hover:underline">
                     {res.title || res.url}
                   </a>
                   <div className="text-[11px] text-muted-foreground/60 max-w-[120px] shrink-0 truncate">
                     {res.url ? getHostname(res.url) : ""}
                   </div>
                 </div>
              ))
            ) : (
               <div className="px-3 py-2 text-[12px] text-muted-foreground whitespace-pre-wrap max-h-[150px] overflow-y-auto">
                 {resultString || "No results"}
               </div>
            )}
          </div>
        </div>
      </CollapsibleContent>
    </Collapsible>
  );
}

function getHostname(url: string) {
  try {
    return new URL(url).hostname;
  } catch {
    return url;
  }
}
