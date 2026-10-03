import { useEffect, useRef, useState, type RefObject } from "react";
import {
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  Folder,
  FolderOpen,
  FolderOutput,
  FolderPlus,
  MessageSquare,
  MessageSquarePlus,
  Pencil,
  Plus,
  Search,
  Trash2,
} from "lucide-react";
import type { ChatView, Project, SessionSummary } from "../../types";
import { accentFold } from "./threadItems";

type Editing = { kind: "project" | "session"; id: string; value: string } | null;

interface ChatSidebarProps {
  collapsed: boolean;
  onToggleCollapsed: () => void;
  search: string;
  onSearchChange: (v: string) => void;
  searchInputRef: RefObject<HTMLInputElement>;
  loadError: string | null;

  projects: Project[];
  standaloneSessions: SessionSummary[];
  sessionsByProject: Record<string, SessionSummary[]>;
  recentSessions: SessionSummary[];
  expanded: Set<string>;
  onToggleExpand: (pid: string) => void;

  view: ChatView;
  onNewChat: () => void;
  onSelectSession: (sid: string, pid: string | null) => void;

  creatingProject: boolean;
  newProjectName: string;
  onNewProjectNameChange: (v: string) => void;
  onStartCreateProject: () => void;
  onSubmitCreateProject: () => void;
  onCancelCreateProject: () => void;
  onNewChatInProject: (pid: string) => void;
  onDeleteProject: (pid: string, name: string) => void;

  editing: Editing;
  onEditingValueChange: (v: string) => void;
  onStartRenameProject: (pid: string, name: string) => void;
  onStartRenameSession: (sid: string, title: string) => void;
  onCommitEdit: () => void;
  onCancelEdit: () => void;

  onMoveSession: (sid: string, projectId: string | null) => void;
  onDeleteSession: (sid: string, title: string) => void;
}

function projectLabel(name: string): string {
  return name;
}

function sessionLabel(s: SessionSummary): string {
  return s.title || "Cuộc trò chuyện mới";
}

function SessionRow({
  s,
  active,
  isEditing,
  editingValue,
  otherProjects,
  onSelect,
  onEditingValueChange,
  onCommitEdit,
  onCancelEdit,
  onStartRename,
  onMove,
  onDelete,
}: {
  s: SessionSummary;
  active: boolean;
  isEditing: boolean;
  editingValue: string;
  otherProjects: Project[];
  onSelect: () => void;
  onEditingValueChange: (v: string) => void;
  onCommitEdit: () => void;
  onCancelEdit: () => void;
  onStartRename: () => void;
  onMove: (projectId: string | null) => void;
  onDelete: () => void;
}) {
  const [menuOpen, setMenuOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!menuOpen) return;
    const onDocClick = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) setMenuOpen(false);
    };
    document.addEventListener("mousedown", onDocClick);
    return () => document.removeEventListener("mousedown", onDocClick);
  }, [menuOpen]);

  return (
    <div className={"rail-row" + (active ? " on" : "")}>
      <div className="rail-session" onClick={isEditing ? undefined : onSelect}>
        <MessageSquare size={12} strokeWidth={1.5} />
        {isEditing ? (
          <input
            className="iasin"
            style={{ padding: "3px 6px", fontSize: 12.5, minHeight: 0 }}
            autoFocus
            value={editingValue}
            onClick={(e) => e.stopPropagation()}
            onChange={(e) => onEditingValueChange(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") onCommitEdit();
              if (e.key === "Escape") onCancelEdit();
            }}
            onBlur={onCommitEdit}
          />
        ) : (
          <span className="name">{sessionLabel(s)}</span>
        )}
      </div>
      {!isEditing && (
        <div className="rail-row-actions">
          <button type="button" className="icon-btn" aria-label="Đổi tên" title="Đổi tên" onClick={onStartRename}>
            <Pencil size={12} strokeWidth={1.5} />
          </button>
          <div className="move-menu-wrap" ref={menuRef}>
            <button
              type="button"
              className="icon-btn"
              aria-label="Chuyển vào dự án"
              title="Chuyển vào dự án"
              onClick={() => setMenuOpen((v) => !v)}
            >
              <FolderOutput size={12} strokeWidth={1.5} />
            </button>
            {menuOpen && (
              <div className="move-menu">
                {otherProjects.length === 0 && !s.project_id && <div className="move-menu-empty">Chưa có dự án khác</div>}
                {otherProjects.map((p) => (
                  <div
                    key={p.project_id}
                    className="move-menu-item"
                    onClick={() => {
                      onMove(p.project_id);
                      setMenuOpen(false);
                    }}
                  >
                    <Folder size={12} strokeWidth={1.5} />
                    {projectLabel(p.name)}
                  </div>
                ))}
                {s.project_id != null && (
                  <div
                    className="move-menu-item danger"
                    onClick={() => {
                      onMove(null);
                      setMenuOpen(false);
                    }}
                  >
                    <FolderOutput size={12} strokeWidth={1.5} />
                    Bỏ khỏi dự án
                  </div>
                )}
              </div>
            )}
          </div>
          <button type="button" className="icon-btn danger" aria-label="Xoá" title="Xoá" onClick={onDelete}>
            <Trash2 size={12} strokeWidth={1.5} />
          </button>
        </div>
      )}
    </div>
  );
}

export default function ChatSidebar(props: ChatSidebarProps) {
  const {
    collapsed,
    onToggleCollapsed,
    search,
    onSearchChange,
    searchInputRef,
    loadError,
    projects,
    standaloneSessions,
    sessionsByProject,
    recentSessions,
    expanded,
    onToggleExpand,
    view,
    onNewChat,
    onSelectSession,
    creatingProject,
    newProjectName,
    onNewProjectNameChange,
    onStartCreateProject,
    onSubmitCreateProject,
    onCancelCreateProject,
    onNewChatInProject,
    onDeleteProject,
    editing,
    onEditingValueChange,
    onStartRenameProject,
    onStartRenameSession,
    onCommitEdit,
    onCancelEdit,
    onMoveSession,
    onDeleteSession,
  } = props;

  const activeSid = view.kind === "session" ? view.sid : null;

  if (collapsed) {
    return (
      <aside className="rail rail-mini">
        <div className="rail-head">
          <button className="iasbtn ghost rail-collapse-btn" onClick={onToggleCollapsed} aria-label="Mở rộng rail">
            <ChevronRight size={13} strokeWidth={1.5} />
          </button>
        </div>
        <div className="rail-mini-actions">
          <button type="button" className="rail-collapsed-item" title="Cuộc trò chuyện mới" onClick={onNewChat}>
            <MessageSquarePlus size={16} strokeWidth={1.5} />
          </button>
          <button
            type="button"
            className="rail-collapsed-item"
            title="Tìm cuộc trò chuyện…"
            onClick={() => {
              onToggleCollapsed();
              requestAnimationFrame(() => searchInputRef.current?.focus());
            }}
          >
            <Search size={16} strokeWidth={1.5} />
          </button>
        </div>
        <div className="rail-collapsed-list">
          {recentSessions.map((s) => (
            <div
              key={s.session_id}
              title={sessionLabel(s)}
              className={"rail-collapsed-item" + (activeSid === s.session_id ? " on" : "")}
              onClick={() => onSelectSession(s.session_id, s.project_id ?? null)}
            >
              <MessageSquare size={14} strokeWidth={1.5} />
            </div>
          ))}
        </div>
        <button
          type="button"
          title="Dự án mới"
          className="rail-collapsed-item rail-collapsed-new"
          onClick={() => {
            onToggleCollapsed();
            onStartCreateProject();
          }}
        >
          <FolderPlus size={14} strokeWidth={1.5} />
        </button>
      </aside>
    );
  }

  const query = accentFold(search.trim());
  const filterActive = query.length > 0;
  const projectMatches = (p: Project) => accentFold(p.name).includes(query);
  const sessionMatches = (s: SessionSummary) => accentFold(sessionLabel(s)).includes(query);

  const visibleStandalone = filterActive ? standaloneSessions.filter(sessionMatches) : standaloneSessions;

  function projectSessionRows(p: Project): SessionSummary[] {
    const rows = sessionsByProject[p.project_id] || [];
    if (!filterActive || projectMatches(p)) return rows;
    return rows.filter(sessionMatches);
  }
  function projectVisible(p: Project): boolean {
    if (!filterActive) return true;
    return projectMatches(p) || (sessionsByProject[p.project_id] || []).some(sessionMatches);
  }

  return (
    <aside className="rail">
      <div className="rail-head">
        <div className="rail-search">
          <Search size={12} strokeWidth={1.5} />
          <input
            ref={searchInputRef}
            className="iasin"
            placeholder="Tìm cuộc trò chuyện…"
            value={search}
            onChange={(e) => onSearchChange(e.target.value)}
          />
        </div>
        <button className="iasbtn ghost rail-collapse-btn" onClick={onToggleCollapsed} aria-label="Thu gọn rail">
          <ChevronLeft size={13} strokeWidth={1.5} />
        </button>
      </div>

      <div className="rail-list">
        <button className="iasbtn rail-new" onClick={onNewChat}>
          <MessageSquarePlus size={14} strokeWidth={1.5} />
          Cuộc trò chuyện mới
        </button>

        {loadError && <div className="rail-load-error">{loadError}</div>}

        <div className="rail-section-head">
          <span className="rail-section-title">Dự án</span>
          <button type="button" className="icon-btn" aria-label="Dự án mới" title="Dự án mới" onClick={onStartCreateProject}>
            <Plus size={13} strokeWidth={1.5} />
          </button>
        </div>

        {creatingProject && (
          <div className="rail-new-row">
            <input
              className="iasin"
              autoFocus
              placeholder="Tên dự án…"
              value={newProjectName}
              onChange={(e) => onNewProjectNameChange(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") onSubmitCreateProject();
                if (e.key === "Escape") onCancelCreateProject();
              }}
              onBlur={onSubmitCreateProject}
            />
          </div>
        )}

        {projects.length === 0 && <div className="rail-empty">Chưa có dự án nào.</div>}

        {projects.filter(projectVisible).map((p) => {
          const isOpen = filterActive ? true : expanded.has(p.project_id);
          const isEditingThis = editing?.kind === "project" && editing.id === p.project_id;
          const rows = projectSessionRows(p);
          return (
            <div key={p.project_id}>
              <div className={"rail-row" + (isEditingThis ? "" : "")}>
                <div className="rail-project" onClick={() => onToggleExpand(p.project_id)}>
                  {isOpen ? <ChevronDown size={13} strokeWidth={1.5} color="var(--teal)" /> : <ChevronRight size={13} strokeWidth={1.5} />}
                  {isOpen ? <FolderOpen size={14} strokeWidth={1.5} /> : <Folder size={14} strokeWidth={1.5} />}
                  {isEditingThis ? (
                    <input
                      className="iasin"
                      style={{ padding: "3px 6px", fontSize: 12.5, minHeight: 0 }}
                      autoFocus
                      value={editing.value}
                      onClick={(e) => e.stopPropagation()}
                      onChange={(e) => onEditingValueChange(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter") onCommitEdit();
                        if (e.key === "Escape") onCancelEdit();
                      }}
                      onBlur={onCommitEdit}
                    />
                  ) : (
                    <span className="name">{p.name}</span>
                  )}
                </div>
                {!isEditingThis && (
                  <div className="rail-row-actions">
                    <button
                      type="button"
                      className="icon-btn"
                      aria-label="Đổi tên dự án"
                      title="Đổi tên"
                      onClick={(e) => {
                        e.stopPropagation();
                        onStartRenameProject(p.project_id, p.name);
                      }}
                    >
                      <Pencil size={12} strokeWidth={1.5} />
                    </button>
                    <button
                      type="button"
                      className="icon-btn"
                      aria-label="Cuộc trò chuyện mới trong dự án"
                      title="Cuộc trò chuyện mới trong dự án"
                      onClick={(e) => {
                        e.stopPropagation();
                        onNewChatInProject(p.project_id);
                      }}
                    >
                      <Plus size={13} strokeWidth={1.5} />
                    </button>
                    <button
                      type="button"
                      className="icon-btn danger"
                      aria-label="Xoá dự án"
                      title="Xoá dự án"
                      onClick={(e) => {
                        e.stopPropagation();
                        onDeleteProject(p.project_id, p.name);
                      }}
                    >
                      <Trash2 size={12} strokeWidth={1.5} />
                    </button>
                  </div>
                )}
              </div>
              {isOpen && (
                <div className="rail-sessions">
                  {rows.length === 0 && <div className="rail-empty">Chưa có cuộc trò chuyện nào.</div>}
                  {rows.map((s) => (
                    <SessionRow
                      key={s.session_id}
                      s={s}
                      active={activeSid === s.session_id}
                      isEditing={editing?.kind === "session" && editing.id === s.session_id}
                      editingValue={editing?.kind === "session" && editing.id === s.session_id ? editing.value : ""}
                      otherProjects={projects.filter((op) => op.project_id !== p.project_id)}
                      onSelect={() => onSelectSession(s.session_id, s.project_id ?? null)}
                      onEditingValueChange={onEditingValueChange}
                      onCommitEdit={onCommitEdit}
                      onCancelEdit={onCancelEdit}
                      onStartRename={() => onStartRenameSession(s.session_id, s.title)}
                      onMove={(pid) => onMoveSession(s.session_id, pid)}
                      onDelete={() => onDeleteSession(s.session_id, sessionLabel(s))}
                    />
                  ))}
                </div>
              )}
            </div>
          );
        })}

        <div className="rail-section-head" style={{ marginTop: 10 }}>
          <span className="rail-section-title">Cuộc trò chuyện</span>
        </div>
        {visibleStandalone.length === 0 && <div className="rail-empty">Chưa có cuộc trò chuyện nào.</div>}
        {visibleStandalone.map((s) => (
          <SessionRow
            key={s.session_id}
            s={s}
            active={activeSid === s.session_id}
            isEditing={editing?.kind === "session" && editing.id === s.session_id}
            editingValue={editing?.kind === "session" && editing.id === s.session_id ? editing.value : ""}
            otherProjects={projects}
            onSelect={() => onSelectSession(s.session_id, null)}
            onEditingValueChange={onEditingValueChange}
            onCommitEdit={onCommitEdit}
            onCancelEdit={onCancelEdit}
            onStartRename={() => onStartRenameSession(s.session_id, s.title)}
            onMove={(pid) => onMoveSession(s.session_id, pid)}
            onDelete={() => onDeleteSession(s.session_id, sessionLabel(s))}
          />
        ))}
      </div>
    </aside>
  );
}
