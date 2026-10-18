import { defineRailway, github, project, service, volume } from "railway/iac";

export default defineRailway(() => {
  const webVolume = volume("web-volume", {
    alerts: { usage: { "80": {}, "95": {}, "100": {} } },
    allowOnlineResize: true,
    region: "iad",
    sizeMB: 500,
  });
  const web = service("web", {
    source: github("dongyuliang5-a11y/ai-document-management-"),
    build: { buildEnvironment: "V3", builder: "RAILPACK" },
    start: "python ai-management/server.py",
    healthcheck: "/health",
    healthcheckTimeout: 100,
    replicas: { "iad": 1 },
    volumeMounts: { "/data": webVolume },
  });

  return project("ai-document-management", {
    variables: { managed: false },
    resources: [web, webVolume],
  });
});
