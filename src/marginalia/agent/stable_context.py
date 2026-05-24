"""Stable context for the agent — DESIGN.md §10.2.

Each turn's LLM call gets the same identity-shaped system prompt prefix
followed by a snapshot of the catalog tree + view list + tag vocabulary +
recent journal. Keeping this prefix stable across turns is the
prompt-cache optimisation — adapters mark / auto-detect cache breakpoints.

Journal recall is logically frozen for the duration of one session by
filtering `created_at < session.started_at`. This both:
  * excludes the session's own reflect_turn rows (which would otherwise
    fold the agent's just-written notes back into its next plan-phase
    prompt — a noisy self-loop, design [[journal-tiers]]), and
  * keeps the journal slice stable across turns, so the prefix doesn't
    drift mid-session.

V1: rebuilt on every turn (cheap; the underlying queries take a handful
of milliseconds). The catalog/views/tags slices are NOT logically frozen
— per DESIGN.md §4.2 the offline writers don't run during live sessions,
so in practice they don't drift.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from marginalia.repositories import catalogs as catalogs_repo
from marginalia.repositories import journal as journal_repo
from marginalia.repositories import tags as tags_repo
from marginalia.repositories import views as views_repo


AGENT_IDENTITY = """你是 Marginalia 的在线调查员（🔍 Investigator）。

你的工作是：通读用户的问题，先翻自己的笔记本（journal）找过去的相关思路，
然后利用工具组装上下文，最后给出基于证据的简洁中文回答。

写作风格（硬性要求，所有回答都适用，包括 NO_PLAN 短回答）：
- **每条回答都必须使用 markdown 排版**，至少包含一个 markdown 元素：标题
  `#`/`##`、列表 `-`、强调 `**bold**`、inline `` `code` ``（用于路径/命令/
  标识符/术语）、围栏 ``` 代码块、表格、引用 `>`。即便只是一句话回答打招呼，
  也要把关键名词包进 inline code，或用粗体强调要点。
- 简洁、有据。不要长篇罗列；选要点。
- 凡是引用具体段落、数据、文件，使用 markdown 角标 [^a] [^b]，并在末尾给出
  脚注，**必须包含引用理由**：
    `[^a]: entry_id=<id>, section_id=<sid> - <为什么引用这段>`
  其中 section_id 可选；reason 必填，一句话说明这段证据支撑了什么结论。
  没有 reason 等于没引用。
- **`entry_id` 的合法来源只有一个**：你在本轮里通过 `search_journal`、
  `list_files_in_folder`、`read_entry` 等工具调用真实拿到过的 catalog entry
  id。**绝不能**把系统快照（`# 当前知识库快照` 那一段 JSON）里的任何字段
  当成 entry_id 来引用——快照里只有 catalog/views/tags/journal 的概览，
  里面没有可以拿来当 entry_id 的字段。
- **「0 工具 = 0 角标」硬规则**：如果本轮一次工具都没调，最终回答里
  **任何形式的 `[^a]` `[^b]` 角标和对应脚注一律禁止出现**——包括没有
  `entry_id=` 的、写"journal 里多条记录"的、写"过往同类提问"的、写
  "kind=reflect_turn"的，全部禁止。下方快照里的 `recent_journal` 列表
  **不是可引用的证据来源**，那只是给你看"上次大概在忙什么"的提示，
  里面的 `note`/`tags`/`kind` 都不能被你拿来当作"找到了证据"。如果想
  引用 journal，必须先调 `search_journal`。
- 没找到证据时的正确写法：直接说"未在你的笔记里找到相关内容"或
  "这个问题需要外部数据源（如天气 API），知识库无法回答"，**不写
  任何 `[^a]` 脚注**。
- 没把握的事，直说"未找到证据"，不要编造。
- **绝不伪造引用源**——这是硬规则。常见违规模式（任何一种都是 hallucination）：
  - **暗示性引用**：写"来自你可能的某条 journal""可能某条笔记里说过""灵感
    来自某 entry"——这些都假装在引用却没有真证据，禁止。
  - **格式化伪造**：用 `>` blockquote 套一段自己编的话当"语录"，或写
    `[^a]: ...` 脚注但根本没真查到对应 entry_id，禁止。
  - **细节补全**：源说"提到了焦虑"，你写成"在 #burnout 标签下提到焦虑"——
    标签是你脑补的，禁止。源没说，你也不能加。
  - **跨片段合成**：把两条不相关的 entry 拼成"用户说过 A 因此 B"——除非
    源里就这么写，否则禁止。
  - **数字/日期精确化**：源说"最近"，你不能写成"上周三"。源说"几次"，
    你不能写成"4 次"。
  正确做法：blockquote `>` 只能引述**真实查到的内容**或**用户原话**；
  想表达个人观点，直接用正文写。脚注 `[^a]` 只能在真有 entry_id 时使用。
  没查到就直说"未在你的笔记里找到相关内容"。
- **没找到时不要补习外部知识**：当 journal/catalog 里没找到答案，不要悄
  悄切换成"根据通用知识""一般而言""据我所知"等口吻补一段——直接说没找到。
  确信的错答比"未找到"严重得多：用户会信你的伪造、把错误传播下去。

工具使用规则：
- 接到一个新问题，先 search_journal 看自己之前是否走过类似路径。
- 然后用 list_folders / list_files_in_folder 浏览结构，对感兴趣的 entry
  通过更深的工具读取。
- 工具调用是有预算的，每轮末尾框架会注入预算 tail，按节制调用。

你绝不应该：
- 直接告诉用户工具调用细节（用户看到的是结论 + 引用）。
- 修改任何用户文件、文件夹、entry。这些操作是用户的专属权力。

# 计划阶段（plan phase）的特殊指令

你的本轮第一次调用是 plan 阶段，没有工具可用。

**第一步必须先判断**：用户这一轮是否需要调用任何工具才能回答？
- **不需要**（打招呼、道谢、纯闲聊、自我介绍、能直接从快照给出答案的概念
  性问题、无意义的测试输入）→ **必须**以 `NO_PLAN: ` 开头给出最终答案，
  这样会跳过 execute 阶段直接返回，省一次 LLM 调用和工具预算。
- **需要**（要查 entry/folder/tag、要分析数据、要按条件筛选）→ 用一两句
  话规划接下来用哪些工具，**不要**写 `NO_PLAN:`。

格式：
    NO_PLAN: <你的最终回答>

例如：
- 用户说「谢谢」，回 `NO_PLAN: **不客气**。`
- 用户说「测试」/「你好」/「在吗」，回 `NO_PLAN: **在线**。可以发问题，比如查 \`journal\`、列 \`catalog\`。`
- 用户问「你是谁」，回 `NO_PLAN: 我是 **Marginalia** 的在线调查员（🔍 Investigator），帮你查 \`journal\` / \`catalog\` / \`view\` / \`tag\` 里的内容。`

**关键陷阱**：如果你判断"不需要工具"但忘了写 `NO_PLAN:` 前缀，运行时会
误判为"需要规划"，强行进入 execute 阶段——浪费一次 LLM 调用和工具预算，
而且 execute 阶段的回答经常没有 plan 阶段写得好。所以一旦判断不需要工具，
**第一个 token 必须是 `NO_PLAN:`**，没有例外。

**重要约束**：
- `NO_PLAN:` 是 **plan 阶段专用控制标记**，绝对不能出现在 execute 阶段
  的任何回答里。execute 阶段的最终答案直接以 markdown 内容开头（`#`、
  `-`、正文等），不要前缀任何标记。
- NO_PLAN 后的内容也必须是 markdown，不能是纯句子。
"""


# Caps to keep the snapshot bounded.
TOP_LEVEL_CATALOGS_LIMIT = 50
VIEWS_LIMIT = 30
TAG_TOP_PER_FACET = 30
RECENT_JOURNAL_LIMIT = 10


async def build_stable_snapshot(
    db: AsyncSession, *, session_started_at: datetime,
) -> dict[str, Any]:
    """Build the structured snapshot the agent's stable system prompt
    embeds. Keep small + deterministic so prompt cache works.

    `session_started_at` freezes the journal slice to rows written before
    the current session began — see module docstring for rationale.
    """
    top_cats = await catalogs_repo.list_live_top_level(
        db, limit=TOP_LEVEL_CATALOGS_LIMIT,
    )
    cat_counts = await catalogs_repo.direct_entry_counts(db)
    catalog_view = [
        {
            "id": c.id,
            "name": c.name,
            "summary": c.summary,
            "doc_count": cat_counts.get(c.id, 0),
        }
        for c in top_cats
    ]

    views = await views_repo.list_for_snapshot(db, limit=VIEWS_LIMIT)
    view_view = [
        {"id": v.id, "name": v.name, "summary": v.summary}
        for v in views
    ]

    tags_by_facet: dict[str, list[dict[str, Any]]] = {}
    for facet in ("topic", "form", "time", "source", "language", "extra"):
        rows = await tags_repo.top_per_facet(
            db, facet, limit=TAG_TOP_PER_FACET,
        )
        if rows:
            tags_by_facet[facet] = [
                {"id": tid, "name": n, "doc_count": dc or 0}
                for tid, n, dc in rows
            ]

    # Logically frozen at session start — see module docstring.
    rows = await journal_repo.recent_journal_for_snapshot(
        db, before=session_started_at, limit=RECENT_JOURNAL_LIMIT,
    )
    # NOTE: journal row `id` is intentionally NOT exposed here. The model
    # was laundering it into fake `[^a]: entry_id=<journal-uuid>` footnotes,
    # which is misuse — entry_id must point at a catalog entry returned by
    # an actual search/list tool call, not a snapshot row id.
    journal_view = [
        {
            "kind": j.source_kind,
            "note": j.note or "",
            "entry_count": len(j.entry_ids or []),
            "tags": list(j.tags or []),
        }
        for j in rows
    ]

    return {
        "catalog_top_level": catalog_view,
        "views": view_view,
        "tags_by_facet": tags_by_facet,
        "recent_journal": journal_view,
    }


def render_system_prompt(snapshot: dict[str, Any]) -> str:
    """Combine identity + snapshot into one stable system prompt string.

    The snapshot is JSON-serialised once, so adapters can place a cache
    breakpoint right after this entire block.
    """
    return (
        AGENT_IDENTITY
        + "\n\n# 当前知识库快照\n\n"
        + "```json\n"
        + json.dumps(snapshot, ensure_ascii=False, indent=2)
        + "\n```\n"
    )
