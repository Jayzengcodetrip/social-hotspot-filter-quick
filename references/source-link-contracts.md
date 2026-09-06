# Selected Core-Link Contracts

Use this reference after ranking fixes the selected topics and before quick report generation. The link gate applies only to eligible items displayed under the selected topics; it does not require every non-selected candidate to have a URL.

## Required and exempt sources

| Source | Selected-item link rule | Allowed source host |
| -- | -- | -- |
| Weibo | Required | `s.weibo.com` |
| Zhihu | Required | `zhihu.com` or a subdomain |
| Baidu | Required | `baidu.com` or a subdomain |
| Jiemian News | Required | `jiemian.com` or a subdomain |
| The Beijing News | Required | `bjnews.com.cn` or a subdomain |
| Caixin | Required | `caixin.com` or a subdomain |
| People's Daily | Required | `people.com.cn` or a subdomain |
| Bilibili | Exempt | URL may be `null` |
| Douyin | Exempt | URL may be `null` |

The fixed source entry page is never a selected-item link. A valid link must identify the corresponding list entry, question, search-entry jump, article, or newspaper story. Baidu's own hot-list jump may lead to a Baidu search page; display it only as the Baidu list-entry link. It is a navigation link, not independently verified factual evidence. Following that observed core-entry URL is allowed; issuing separate supplementary searches is not part of the quick edition.

## Capture workflow

For Weibo, Zhihu, and Baidu:

1. Extract each row's exact title and its `href` together from the same DOM row. Do not collect titles first and silently leave URLs for later.
   Preserve this pairing through the lossless packet transport in [collection-runtime.md](collection-runtime.md); the importer saves the actual browser row array and the builder normalizes only observed hrefs. ID-based link backfills require observed evidence and must not alter the original title.
2. Prefer the anchor's resolved `href`. When only a raw relative or protocol-relative value is available, normalize it with:

```text
python3 <skill-dir>/scripts/normalize_core_link.py <source-id> '<raw-href>'
```

3. If the row exposes no usable anchor, preserve the observed missing/unusable-link state during collection. Once selected, click that exact row when needed and record the final URL reached; restore the list page. Do not click every non-selected row merely to obtain links. Earlier destination reading is allowed only when needed for reliable event identity.
4. Never construct or guess a URL from the title. If a selected item still lacks a valid platform URL after its same-row check, stop publication and report that item's ID. A non-selected missing URL alone is not a reason for a second browsing pass or dropping its title from the candidate pool.

For Jiemian News, The Beijing News, Caixin, and People's Daily, retain each article URL observed alongside its headline on the current homepage or current-day issue page. Opening every article body is not required. Complete missing selected links only when necessary; a selected article without a valid same-platform URL still fails the link gate.

Bilibili and Douyin are explicitly exempt. Keep the exact directly observed title and use `url: null` when no human-usable item link is available.

## Publication gate

After ranking and any required link backfill, run:

```text
python3 <skill-dir>/scripts/validate_selected_links.py <ledger.json> --limit 10
```

The report generator and final report validator call the same rules internally. Skipping this standalone command therefore cannot produce a publishable report with missing required links.

The mechanical checks validate presence, absolute HTTP(S) form, expected source host, and rejection of the fixed source entry page. They cannot prove that the browser truly exposed the link or that the title-link pairing is semantically correct. Preserve the same-row extraction evidence and never fabricate browser actions.
