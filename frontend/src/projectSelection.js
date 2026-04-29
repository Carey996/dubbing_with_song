export function selectAllProjectIds(projects) {
  return projects.map((project) => project.id);
}

export function toggleProjectSelection(selectedIds, projectId) {
  if (selectedIds.includes(projectId)) {
    return selectedIds.filter((id) => id !== projectId);
  }
  return [...selectedIds, projectId];
}

export function normalizeSelectedProjectIds(projects, selectedIds) {
  const projectIds = new Set(projects.map((project) => project.id));
  return selectedIds.filter((id) => projectIds.has(id));
}

export function areAllProjectsSelected(projects, selectedIds) {
  return projects.length > 0 && projects.every((project) => selectedIds.includes(project.id));
}
