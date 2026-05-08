"use client";

import { useEffect, useMemo, useState, type ChangeEvent } from "react";

import {
  askFollowUp,
  createPlan,
  deletePlan,
  generatePlan,
  getPlan,
  getPlanMessages,
  listPlans,
  type FollowUpResponse,
  type PlanMessage,
  type PlanGenerateResponse,
  type PlanResponse,
  type PlanSummary
} from "../lib/api";

const examples = [
  "我下周末想去北京拍一组写真，请帮我安排行程和拍摄方案，想包含日出和日落。",
  "我下周末想去长城拍日出日落，人像写真为主，用 iPhone 拍，请安排两天路线。",
  "我明天去厦门，想拍海边白裙、沙滩、日落和一点电影感，用手机拍。",
  "如果一直下雨，就不要硬编晴天方案，请告诉我哪些需求无法满足。"
];

const ACCEPTED_IMAGE_TYPES = new Set(["image/jpeg", "image/png", "image/webp"]);
const MAX_UPLOAD_IMAGE_COUNT = 3;
const MAX_REFERENCE_IMAGE_CHARS = 2_700_000;
const MAX_SOURCE_IMAGE_BYTES = 12 * 1024 * 1024;
const MAX_IMAGE_DIMENSION = 1600;

type PlanView = (PlanGenerateResponse | PlanResponse) & {
  user_input?: string;
  reference_images?: string[];
};

type ConversationItem = {
  id: string;
  role: "user" | "assistant" | string;
  content: string;
  created_at?: string | null;
  warnings?: string[];
};

function textValue(value: unknown, fallback = "待确认") {
  if (Array.isArray(value)) {
    return value.length ? value.join("、") : fallback;
  }
  if (typeof value === "string" && value.trim()) {
    return value;
  }
  if (typeof value === "number") {
    return String(value);
  }
  return fallback;
}

function countItems(value: unknown) {
  return Array.isArray(value) ? value.length : 0;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function loadImageFromFile(file: File) {
  return new Promise<HTMLImageElement>((resolve, reject) => {
    const url = URL.createObjectURL(file);
    const image = new Image();
    image.onload = () => {
      URL.revokeObjectURL(url);
      resolve(image);
    };
    image.onerror = () => {
      URL.revokeObjectURL(url);
      reject(new Error("图片无法读取，请使用 JPG、PNG 或 WebP。"));
    };
    image.src = url;
  });
}

async function readFileAsCompressedDataUrl(file: File) {
  if (!ACCEPTED_IMAGE_TYPES.has(file.type)) {
    throw new Error("仅支持 JPG、PNG、WebP 参考图。");
  }
  if (file.size > MAX_SOURCE_IMAGE_BYTES) {
    throw new Error("单张原图超过 12MB，请先压缩后上传。");
  }

  const image = await loadImageFromFile(file);
  const scale = Math.min(1, MAX_IMAGE_DIMENSION / Math.max(image.naturalWidth, image.naturalHeight));
  const width = Math.max(1, Math.round(image.naturalWidth * scale));
  const height = Math.max(1, Math.round(image.naturalHeight * scale));
  const canvas = document.createElement("canvas");
  canvas.width = width;
  canvas.height = height;
  const context = canvas.getContext("2d");
  if (!context) {
    throw new Error("浏览器无法压缩图片。");
  }
  context.fillStyle = "#fff";
  context.fillRect(0, 0, width, height);
  context.drawImage(image, 0, 0, width, height);

  for (const quality of [0.82, 0.72, 0.62, 0.52]) {
    const dataUrl = canvas.toDataURL("image/jpeg", quality);
    if (dataUrl.length <= MAX_REFERENCE_IMAGE_CHARS) {
      return dataUrl;
    }
  }
  throw new Error("图片压缩后仍然过大，请换一张更小的参考图。");
}

function shortDate(value?: string | null) {
  if (!value) {
    return "";
  }
  return value.replace("T", " ").slice(0, 16);
}

function durationText(value?: number | null) {
  if (!value || value < 0) {
    return "";
  }
  if (value < 1000) {
    return `${value} ms`;
  }
  return `${(value / 1000).toFixed(1)} s`;
}

function toolStats(plan: PlanView | null) {
  const steps = plan?.agent_steps ?? [];
  const failed = steps.filter((step) => isRecord(step.tool_output) && step.tool_output.success === false).length;
  return { total: steps.length, failed };
}

function imageAnalysisSummary(value: unknown) {
  if (!isRecord(value)) {
    return "";
  }
  const keys = [
    "style_summary",
    "description",
    "lighting",
    "composition",
    "pose_action",
    "color_palette",
    "clothing_props",
    "location_types",
    "replication_notes"
  ];
  const parts: string[] = [];
  for (const key of keys) {
    const item = value[key];
    if (Array.isArray(item)) {
      parts.push(...item.map((part) => String(part).trim()).filter(Boolean).slice(0, 2));
    } else if (typeof item === "string" && item.trim()) {
      parts.push(item.trim());
    }
  }
  return Array.from(new Set(parts)).slice(0, 3).join("；");
}

function conversationFromPlan(plan: PlanView | null, messages: PlanMessage[]): ConversationItem[] {
  if (!plan) {
    return [];
  }
  const items: ConversationItem[] = [];
  if (plan.user_input) {
    items.push({
      id: `${plan.plan_id}-initial-user`,
      role: "user",
      content: plan.user_input,
      created_at: "created_at" in plan ? plan.created_at : null
    });
  }
  if (plan.final_markdown) {
    items.push({
      id: `${plan.plan_id}-initial-assistant`,
      role: "assistant",
      content: plan.final_markdown,
      created_at: "updated_at" in plan ? plan.updated_at : null,
      warnings: plan.warnings
    });
  }
  for (const message of messages) {
    items.push({
      id: message.id,
      role: message.role,
      content: message.content,
      created_at: message.created_at,
      warnings: message.warnings
    });
  }
  return items;
}

export function PlanWorkbench() {
  const [userInput, setUserInput] = useState(examples[0]);
  const [referenceImages, setReferenceImages] = useState<string[]>([]);
  const [followUpImages, setFollowUpImages] = useState<string[]>([]);
  const [followUpQuestion, setFollowUpQuestion] = useState("");
  const [followUpResult, setFollowUpResult] = useState<FollowUpResponse | null>(null);
  const [history, setHistory] = useState<PlanSummary[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [isHistoryLoading, setIsHistoryLoading] = useState(false);
  const [isFollowUpLoading, setIsFollowUpLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [plan, setPlan] = useState<PlanView | null>(null);
  const [messages, setMessages] = useState<PlanMessage[]>([]);

  const metrics = useMemo(() => {
    const stats = toolStats(plan);
    return [
      ["状态", plan?.status ?? "待生成"],
      ["路线", `${plan?.route?.length ?? 0} 段`],
      ["工具", `${stats.total} 次`],
      ["失败", `${stats.failed} 次`]
    ];
  }, [plan]);

  const conversation = useMemo(() => conversationFromPlan(plan, messages), [plan, messages]);

  useEffect(() => {
    void refreshHistory();
  }, []);

  async function refreshHistory() {
    setIsHistoryLoading(true);
    try {
      setHistory(await listPlans());
    } catch (err) {
      setError(err instanceof Error ? err.message : "历史记录读取失败");
    } finally {
      setIsHistoryLoading(false);
    }
  }

  async function handleSubmit() {
    if (isLoading) {
      return;
    }
    if (!userInput.trim()) {
      setError("请输入旅拍需求");
      return;
    }
    setIsLoading(true);
    setError(null);
    setFollowUpResult(null);
    try {
      const created = await createPlan(userInput.trim(), referenceImages);
      const generated = await generatePlan(created.plan_id);
      setPlan({ ...generated, user_input: userInput.trim(), reference_images: referenceImages });
      setMessages([]);
      await refreshHistory();
    } catch (err) {
      setError(err instanceof Error ? err.message : "生成失败，请检查 API 服务或模型配置");
    } finally {
      setIsLoading(false);
    }
  }

  async function openHistory(planId: string) {
    setError(null);
    try {
      const [loadedPlan, loadedMessages] = await Promise.all([getPlan(planId), getPlanMessages(planId)]);
      setPlan(loadedPlan);
      setMessages(loadedMessages);
      setUserInput(loadedPlan.user_input);
      setReferenceImages(loadedPlan.reference_images ?? []);
      setFollowUpResult(null);
      setFollowUpQuestion("");
      setFollowUpImages([]);
    } catch (err) {
      setError(err instanceof Error ? err.message : "方案读取失败");
    }
  }

  async function removeHistory(planId: string) {
    setError(null);
    try {
      await deletePlan(planId);
      if (plan?.plan_id === planId) {
        setPlan(null);
        setMessages([]);
      }
      await refreshHistory();
    } catch (err) {
      setError(err instanceof Error ? err.message : "删除失败");
    }
  }

  async function handleFollowUp() {
    if (!plan || !followUpQuestion.trim()) {
      setError("请选择一个方案并输入追问");
      return;
    }
    setIsFollowUpLoading(true);
    setError(null);
    try {
      const result = await askFollowUp(plan.plan_id, followUpQuestion.trim(), followUpImages);
      setFollowUpResult(result);
      setMessages(result.messages);
      setFollowUpQuestion("");
      setFollowUpImages([]);
      await refreshHistory();
    } catch (err) {
      setError(err instanceof Error ? err.message : "追问失败，请检查模型或工具配置");
    } finally {
      setIsFollowUpLoading(false);
    }
  }

  async function handleImageChange(event: ChangeEvent<HTMLInputElement>, target: "main" | "followup") {
    const current = target === "main" ? referenceImages : followUpImages;
    const remaining = MAX_UPLOAD_IMAGE_COUNT - current.length;
    if (remaining <= 0) {
      setError(`最多上传 ${MAX_UPLOAD_IMAGE_COUNT} 张参考图。`);
      event.target.value = "";
      return;
    }
    const selectedFiles = Array.from(event.target.files ?? []);
    const files = selectedFiles.slice(0, remaining);
    if (!files.length) {
      event.target.value = "";
      return;
    }
    try {
      const images = await Promise.all(files.map(readFileAsCompressedDataUrl));
      if (target === "main") {
        setReferenceImages((items) => [...items, ...images].slice(0, MAX_UPLOAD_IMAGE_COUNT));
      } else {
        setFollowUpImages((items) => [...items, ...images].slice(0, MAX_UPLOAD_IMAGE_COUNT));
      }
      setError(selectedFiles.length > files.length ? `最多上传 ${MAX_UPLOAD_IMAGE_COUNT} 张，已保留前 ${files.length} 张。` : null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "图片读取失败");
    } finally {
      event.target.value = "";
    }
  }

  function removeImage(index: number, target: "main" | "followup") {
    if (target === "main") {
      setReferenceImages((items) => items.filter((_, itemIndex) => itemIndex !== index));
    } else {
      setFollowUpImages((items) => items.filter((_, itemIndex) => itemIndex !== index));
    }
  }

  return (
    <main className="shell">
      <div className="app-frame">
        <section className="panel composer">
          <div className="brand-row">
            <div className="brand">
              <h1>旅拍助手 Agent</h1>
              <p>LLM-led Planning Console</p>
            </div>
            <span className="status-pill">Tools + History</span>
          </div>

          <div className="field">
            <label htmlFor="travel-shot-input">旅拍需求</label>
            <textarea
              id="travel-shot-input"
              className="input"
              value={userInput}
              onChange={(event) => setUserInput(event.target.value)}
            />
          </div>

          <ImageUpload
            images={referenceImages}
            label="参考图"
            onChange={(event) => void handleImageChange(event, "main")}
            onRemove={(index) => removeImage(index, "main")}
          />

          <div className="actions">
            <button className="primary-button" onClick={handleSubmit} disabled={isLoading}>
              {isLoading ? "生成中" : "生成方案"}
            </button>
            <button
              className="secondary-button"
              onClick={() => {
                setPlan(null);
                setMessages([]);
                setReferenceImages([]);
                setFollowUpResult(null);
              }}
              type="button"
            >
              清空
            </button>
          </div>

          {error ? <div className="error">{error}</div> : null}

          <div className="example-list">
            {examples.map((example) => (
              <button className="example-button" key={example} onClick={() => setUserInput(example)} type="button">
                {example}
              </button>
            ))}
          </div>

          <section className="history-block">
            <div className="section-title">
              <h2>历史记录</h2>
              <button className="text-button" onClick={() => void refreshHistory()} type="button">
                {isHistoryLoading ? "刷新中" : "刷新"}
              </button>
            </div>
            <div className="history-list">
              {history.length ? (
                history.map((item) => (
                  <div className="history-item" key={item.plan_id}>
                    <button onClick={() => void openHistory(item.plan_id)} type="button">
                      <strong>{item.destination ?? "未确认目的地"}</strong>
                      <span>{textValue(item.date_range)} · {item.status}</span>
                      <p>{item.user_input}</p>
                    </button>
                    <button aria-label="删除历史记录" className="delete-button" onClick={() => void removeHistory(item.plan_id)} type="button">
                      删除
                    </button>
                  </div>
                ))
              ) : (
                <p className="subtle">暂无历史记录</p>
              )}
            </div>
          </section>
        </section>

        <section className="panel workspace">
          <header className="workspace-header">
            <div>
              <h2>方案工作台</h2>
              <p>只展示关键结论、路线、风险和可追溯工具结果。</p>
            </div>
            <div className="metric-row">
              {metrics.map(([label, value]) => (
                <div className="metric" key={label}>
                  <span>{label}</span>
                  <strong>{value}</strong>
                </div>
              ))}
            </div>
          </header>

          {!plan ? (
            <div className="empty-state">输入需求后生成第一版旅拍方案，或从历史记录打开已有方案。</div>
          ) : (
            <div className="content-grid">
              <section className="result-panel">
                <h3>最终方案</h3>
                {plan.warnings?.length ? (
                  <div className="warning-list">
                    {plan.warnings.map((warning) => (
                      <p key={warning}>{warning}</p>
                    ))}
                  </div>
                ) : null}
                <div className="markdown">{plan.final_markdown || "当前方案还没有生成最终内容。"}</div>
              </section>

              <div className="side-stack">
                <section className="result-panel">
                  <h3>目标与证据</h3>
                  <div className="context-list">
                    <Info label="目的地" value={plan.parsed_goal.destination} />
                    <Info label="日期" value={plan.parsed_goal.date_range} />
                    <Info label="风格" value={plan.parsed_goal.shooting_style} />
                    <Info label="参考图" value={imageAnalysisSummary(plan.image_analysis)} />
                    <Info label="天气" value={plan.weather_context?.summary} />
                    <Info label="光线" value={plan.sunlight_context?.summary} />
                    <Info label="更新时间" value={shortDate("updated_at" in plan ? plan.updated_at : null)} />
                  </div>
                </section>

                <section className="result-panel conversation-panel">
                  <h3>对话记录</h3>
                  <div className="conversation-list">
                    {conversation.length ? (
                      conversation.map((message) => (
                        <div className={`conversation-message ${message.role === "user" ? "from-user" : "from-assistant"}`} key={message.id}>
                          <div className="conversation-meta">
                            <strong>{message.role === "user" ? "你" : "助手"}</strong>
                            {message.created_at ? <span>{shortDate(message.created_at)}</span> : null}
                          </div>
                          <p>{message.content}</p>
                          {message.warnings?.map((warning) => (
                            <p className="inline-warning" key={warning}>{warning}</p>
                          ))}
                        </div>
                      ))
                    ) : (
                      <p className="subtle">暂无追问记录</p>
                    )}
                  </div>
                </section>

                <section className="result-panel">
                  <h3>追问调整</h3>
                  <textarea
                    className="followup-input"
                    value={followUpQuestion}
                    onChange={(event) => setFollowUpQuestion(event.target.value)}
                    placeholder="例如：把第一天改成只拍长城，日出之后加一段咖啡馆室内备选。"
                  />
                  <ImageUpload
                    compact
                    images={followUpImages}
                    label="追问图片"
                    onChange={(event) => void handleImageChange(event, "followup")}
                    onRemove={(index) => removeImage(index, "followup")}
                  />
                  <button className="primary-button" disabled={isFollowUpLoading} onClick={handleFollowUp} type="button">
                    {isFollowUpLoading ? "调整中" : "发送追问"}
                  </button>
                  {followUpResult ? (
                    <div className="followup-result">
                      <strong>{followUpResult.status === "cannot_satisfy" ? "无法满足" : "调整建议"}</strong>
                      <p>{followUpResult.answer}</p>
                      {followUpResult.changes.map((change, index) => (
                        <div className="change-item" key={`${textValue(change.section)}-${index}`}>
                          <span>{textValue(change.section, "改动")}</span>
                          <p>{textValue(change.change)}</p>
                        </div>
                      ))}
                      {followUpResult.warnings.map((warning) => (
                        <p className="inline-warning" key={warning}>{warning}</p>
                      ))}
                    </div>
                  ) : null}
                </section>

                <section className="result-panel">
                  <h3>路线</h3>
                  <div className="route-list">
                    {plan.route?.length ? (
                      plan.route.map((item) => (
                        <div className="route-item" key={item.item_id}>
                          <strong>{item.date ? `${item.date} ` : ""}{item.start_time}-{item.end_time} {item.spot_name}</strong>
                          <p>{item.light_label ? `${item.light_label} · ` : ""}{item.shoot_goal}</p>
                          {item.transfer_to_next?.summary ? <p>下一段：{item.transfer_to_next.summary}</p> : null}
                        </div>
                      ))
                    ) : (
                      <p className="subtle">没有可执行路线，查看最终方案中的原因说明。</p>
                    )}
                  </div>
                </section>

                <details className="result-panel tool-details">
                  <summary>工具轨迹</summary>
                  <div className="step-list">
                    {plan.agent_steps?.length ? (
                      plan.agent_steps.map((step, index) => {
                        const output = isRecord(step.tool_output) ? step.tool_output : {};
                        const failed = output.success === false;
                        const spent = durationText(step.duration_ms);
                        return (
                          <div className="step-item" key={`${step.task_id}-${index}`}>
                            <div>
                              <strong>{step.tool_name ?? step.task_id}</strong>
                              <p>{step.reasoning_summary ?? "已执行"}</p>
                              {spent ? <span>耗时：{spent}</span> : null}
                              {output.error ? <span>{String(output.error)}</span> : null}
                            </div>
                            <span className={`tool-state ${failed ? "warn" : "ok"}`}>{failed ? "失败/降级" : "完成"}</span>
                          </div>
                        );
                      })
                    ) : (
                      <p className="subtle">暂无工具轨迹</p>
                    )}
                  </div>
                </details>
              </div>
            </div>
          )}
        </section>
      </div>
    </main>
  );
}

function Info({ label, value }: { label: string; value: unknown }) {
  return (
    <div>
      <span>{label}</span>
      <strong>{textValue(value)}</strong>
    </div>
  );
}

function ImageUpload({
  compact = false,
  images,
  label,
  onChange,
  onRemove
}: {
  compact?: boolean;
  images: string[];
  label: string;
  onChange: (event: ChangeEvent<HTMLInputElement>) => void;
  onRemove: (index: number) => void;
}) {
  return (
    <div className={`upload-block ${compact ? "compact-upload" : ""}`}>
      <div className="upload-header">
        <span>{label}</span>
        <label className="upload-button">
          上传图片
          <input accept="image/jpeg,image/png,image/webp" multiple onChange={onChange} type="file" />
        </label>
      </div>
      {images.length ? (
        <div className="image-preview-row">
          {images.map((image, index) => (
            <div className="image-preview" key={`${image.slice(0, 24)}-${index}`}>
              <img alt={`${label} ${index + 1}`} src={image} />
              <button aria-label={`移除${label}`} onClick={() => onRemove(index)} type="button">
                x
              </button>
            </div>
          ))}
        </div>
      ) : (
        <p className="upload-empty">JPG/PNG/WebP，最多 3 张，会自动压缩。</p>
      )}
    </div>
  );
}
