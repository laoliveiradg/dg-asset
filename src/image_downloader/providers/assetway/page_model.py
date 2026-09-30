"""Semantic, metadata-only view of an Assetway page over managed CDP."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, replace

from image_downloader.chrome.cdp_client import CdpClient
from image_downloader.chrome.models import CdpError
from image_downloader.providers.assetway.errors import (
    AssetwayDownloadError,
    authentication_required,
)

logger = logging.getLogger(__name__)

_SENSITIVE_METADATA = re.compile(
    r"(cookie|token|authorization|session|secret|password)", re.IGNORECASE
)
_CONTROL_SELECTOR = (
    'button,a,input,select,option,[role="button"],[role="option"],'
    '[role="menuitem"],[role="menuitemradio"],[onclick],[tabindex],div,span'
)
_DOWNLOAD_TERMS = (
    "download", "baixar", "original", "source", "fonte", "arquivo", "file",
    "eps", "ai", "svg", "jpg", "jpeg", "png", "alta", "high", "full",
    "maxima", "maximum",
)

PAGE_INSPECTION_EXPRESSION = r"""
(() => {
  const shortText = value => String(value || "").replace(/\s+/g, " ").trim().slice(0, 160);
  const visible = element => {
    const style = window.getComputedStyle(element);
    const rect = element.getBoundingClientRect();
    return style.display !== "none" && style.visibility !== "hidden" &&
      Number(style.opacity) !== 0 && rect.width > 0 && rect.height > 0;
  };
  const selector = 'button,a,input,select,option,[role="button"],[role="option"],' +
    '[role="menuitem"],[role="menuitemradio"],[onclick],[tabindex],div,span';
  const semanticPattern = new RegExp(
    'download|baixar|original|source|fonte|arquivo|file|eps|svg|' +
    'jpeg|jpg|png|alta|high|full|maxim', 'i');
  const interactiveSelector = 'button,a,input,select,option,[role="button"],[role="option"],' +
    '[role="menuitem"],[role="menuitemradio"],[onclick],[tabindex]';
  const roots = [document];
  let shadowRootCount = 0;
  for (let cursor = 0; cursor < roots.length; cursor += 1) {
    for (const element of roots[cursor].querySelectorAll('*')) {
      if (element.shadowRoot) {
        roots.push(element.shadowRoot);
        shadowRootCount += 1;
      }
    }
  }
  const metadata = element => {
    const svg = element.matches('svg') ? element : element.querySelector('svg');
    const svgLabel = svg ? shortText(
      svg.getAttribute('aria-label') || svg.querySelector('title')?.textContent
    ) : '';
    const dataAction = shortText(
      element.getAttribute('data-action') || element.getAttribute('data-testid') ||
      element.getAttribute('data-label') || element.getAttribute('data-tooltip') ||
      element.getAttribute('data-title')
    );
    const dataNames = Array.from(element.attributes || [])
      .map(attribute => attribute.name)
      .filter(name => name.startsWith('data-'))
      .slice(0, 12)
      .join(' ');
    const dialog = element.closest('[role="dialog"],.ant-modal,[aria-modal="true"]');
    const compactGroup = element.parentElement?.matches('.ant-space-compact') ?
      element.parentElement : null;
    const compactGroups = Array.from(document.querySelectorAll('.ant-space-compact'));
    return {
      tag: element.tagName.toLowerCase(),
      role: shortText(element.getAttribute('role')),
      text: shortText(element.innerText || element.textContent || element.value),
      ariaLabel: shortText(element.getAttribute('aria-label')),
      title: shortText(element.getAttribute('title')),
      popup: shortText(element.getAttribute('aria-haspopup')),
      elementId: shortText(element.id),
      className: shortText(element.className),
      testId: shortText(element.getAttribute('data-testid')),
      dataAction,
      dataNames,
      svgLabel,
      context: dialog ? 'dialog' : 'page',
      groupIndex: compactGroup ? compactGroups.indexOf(compactGroup) : -1,
      siblingIndex: element.parentElement ?
        Array.from(element.parentElement.children).indexOf(element) : -1,
      iconOnly: String(element.className).includes('ant-btn-icon-only'),
      disabled: Boolean(element.disabled) || element.getAttribute('aria-disabled') === 'true'
    };
  };
  const candidates = [];
  const seen = new Set();
  for (const root of roots) {
    for (const element of root.querySelectorAll(selector)) {
      if (seen.has(element) || !visible(element)) continue;
      const info = metadata(element);
      const evidence = Object.values(info).join(' ');
      const interactive = element.matches(interactiveSelector);
      const genericEvidence = [info.ariaLabel, info.title, info.svgLabel,
        info.dataAction, info.dataNames, info.elementId, info.className,
        element.children.length <= 3 && info.text.length <= 80 ? info.text : ''].join(' ');
      if (!interactive && !semanticPattern.test(genericEvidence)) continue;
      if (!interactive) {
        const ancestor = element.closest(interactiveSelector);
        if (ancestor && visible(ancestor) &&
            semanticPattern.test(Object.values(metadata(ancestor)).join(' '))) continue;
      }
      seen.add(element);
      candidates.push(info);
    }
  }
  const fullBodyText = String(document.body?.innerText || document.body?.textContent || '');
  const bodyText = shortText(fullBodyText);
  const passwordVisible = Array.from(document.querySelectorAll('input[type="password"]'))
    .some(visible);
  const loginFormVisible = Array.from(document.querySelectorAll('form'))
    .some(form => visible(form) &&
      /login|entrar|senha|password|sign in/i.test(shortText(form.innerText)));
  return {
    readyState: document.readyState,
    hostname: location.hostname,
    pathname: location.pathname,
    bodyTextLength: fullBodyText.trim().length,
    elementCount: document.querySelectorAll('*').length,
    buttonCount: document.querySelectorAll('button,[role="button"]').length,
    linkCount: document.querySelectorAll('a').length,
    shadowRootCount,
    bodyReady: Boolean(document.body) && (bodyText.length > 0 || document.body.children.length > 0),
    loginRequired: passwordVisible || loginFormVisible ||
      /\/(login|signin|sign-in|auth)(\/|$)/i.test(location.pathname),
    controls: candidates.map((control, index) => ({index, ...control}))
  };
})()
"""


@dataclass(frozen=True, slots=True)
class PageControl:
    index: int
    tag: str
    role: str
    text: str
    aria_label: str
    title: str
    popup: str
    disabled: bool
    element_id: str = ""
    class_name: str = ""
    test_id: str = ""
    data_action: str = ""
    data_names: str = ""
    svg_label: str = ""
    frame_id: str | None = None
    context: str = "page"
    group_index: int = -1
    sibling_index: int = -1
    icon_only: bool = False

    @property
    def display_label(self) -> str:
        return self.aria_label or self.text or self.title or self.svg_label or self.data_action

    @property
    def semantic_label(self) -> str:
        values = (
            self.text, self.aria_label, self.title, self.svg_label, self.data_action,
            self.element_id, self.class_name, self.test_id, self.data_names,
        )
        return " ".join(value for value in values if value)

    @property
    def opens_popup(self) -> bool:
        return bool(self.popup and self.popup.casefold() != "false")


@dataclass(frozen=True, slots=True)
class AssetwayPageSnapshot:
    ready_state: str
    hostname: str
    pathname: str
    login_required: bool
    controls: tuple[PageControl, ...]
    body_text_length: int = 0
    element_count: int = 0
    button_count: int = 0
    link_count: int = 0
    frame_count: int = 1
    shadow_root_count: int = 0
    body_ready: bool = False

    def require_authenticated(self) -> None:
        if self.login_required:
            raise authentication_required()

    def find_download_action(self) -> PageControl:
        scored: list[tuple[int, PageControl]] = []
        for control in self.controls:
            evidence = control.semantic_label.casefold()
            if control.disabled or not re.search(r"\b(download|baixar)\b", evidence):
                continue
            if any(term in evidence for term in ("preview", "thumbnail", "watermark")):
                continue
            score = 0
            if re.search(r"\b(download|baixar)\b", control.display_label.casefold()):
                score += 8
            if control.tag in {"button", "a", "input"} or control.role == "button":
                score += 4
            if control.opens_popup:
                score += 3
            if control.context == "dialog":
                score += 10
            if control.data_action or control.test_id:
                score += 1
            scored.append((score, control))
        if not scored:
            raise AssetwayDownloadError(
                "download_action_unverified",
                "Não foi possível identificar uma ação oficial de download.",
                retryable=False,
            )
        best_score = max(score for score, _ in scored)
        candidates = [control for score, control in scored if score == best_score]
        labels = {control.display_label.casefold() for control in candidates}
        if len(candidates) > 1 and len(labels) > 1:
            raise AssetwayDownloadError(
                "download_action_unverified",
                "Não foi possível identificar uma única ação oficial de download.",
                retryable=False,
            )
        return candidates[0]

    def find_download_menu_trigger(self, action: PageControl) -> PageControl | None:
        if action.group_index < 0:
            return None
        candidates = [
            control
            for control in self.controls
            if control.frame_id == action.frame_id
            and control.group_index == action.group_index
            and control.sibling_index == action.sibling_index + 1
            and control.icon_only
            and not control.disabled
        ]
        return candidates[0] if len(candidates) == 1 else None


class AssetwayPageModel:
    def __init__(self, cdp: CdpClient, target_id: str) -> None:
        self.cdp = cdp
        self.target_id = target_id

    def inspect(self) -> AssetwayPageSnapshot:
        main = self._inspect_context(None)
        frame_ids_method = getattr(self.cdp, "frame_ids", None)
        frame_ids = frame_ids_method(self.target_id) if frame_ids_method is not None else ()
        main_frame_id = frame_ids[0] if frame_ids else None
        controls = list(main.controls)
        shadow_roots = main.shadow_root_count
        login_required = main.login_required
        inspected_frames = 1
        for frame_id in frame_ids:
            if frame_id == main_frame_id:
                continue
            try:
                frame = self._inspect_context(frame_id)
            except (CdpError, AssetwayDownloadError):
                continue
            inspected_frames += 1
            shadow_roots += frame.shadow_root_count
            login_required = login_required or frame.login_required
            controls.extend(frame.controls)
        return replace(
            main,
            login_required=login_required,
            controls=tuple(controls),
            frame_count=inspected_frames,
            shadow_root_count=shadow_roots,
        )

    def _inspect_context(self, frame_id: str | None) -> AssetwayPageSnapshot:
        if frame_id is None:
            result = self.cdp.evaluate_page(self.target_id, PAGE_INSPECTION_EXPRESSION)
        else:
            result = self.cdp.evaluate_frame(
                self.target_id, frame_id, PAGE_INSPECTION_EXPRESSION
            )
        if not isinstance(result, dict) or not isinstance(result.get("controls"), list):
            raise AssetwayDownloadError(
                "page_unverified",
                "A página Assetway não retornou controles verificáveis.",
                retryable=True,
            )
        return AssetwayPageSnapshot(
            ready_state=str(result.get("readyState", "")),
            hostname=str(result.get("hostname", "")).lower(),
            pathname=str(result.get("pathname", "")),
            login_required=bool(result.get("loginRequired", False)),
            controls=tuple(
                replace(self._parse_control(value), frame_id=frame_id)
                for value in result["controls"]
            ),
            body_text_length=self._safe_count(result.get("bodyTextLength")),
            element_count=self._safe_count(result.get("elementCount")),
            button_count=self._safe_count(result.get("buttonCount")),
            link_count=self._safe_count(result.get("linkCount")),
            shadow_root_count=self._safe_count(result.get("shadowRootCount")),
            body_ready=bool(result.get("bodyReady", False)),
        )

    def log_download_diagnostic(
        self,
        snapshot: AssetwayPageSnapshot | None = None,
        *,
        page_state: str = "inspected",
    ) -> int:
        inspected = snapshot or self.inspect()
        candidates = [
            control for control in inspected.controls
            if any(term in control.semantic_label.casefold() for term in _DOWNLOAD_TERMS)
        ]
        logger.info(
            "provider=ASSETWAY page_state=%s ready_state=%s hostname=%s pathname=%s "
            "body_text_length=%s element_count=%s button_count=%s link_count=%s "
            "frame_count=%s shadow_root_count=%s candidate_count=%s controls=%s",
            page_state,
            self._safe_metadata(inspected.ready_state),
            self._safe_metadata(inspected.hostname),
            self._safe_metadata(inspected.pathname),
            inspected.body_text_length,
            inspected.element_count,
            inspected.button_count,
            inspected.link_count,
            inspected.frame_count,
            inspected.shadow_root_count,
            len(candidates),
            len(inspected.controls),
        )
        for position, control in enumerate(candidates):
            logger.info(
                "provider=ASSETWAY dom_candidate=%s tag=%s role=%s text=%s "
                "aria_label=%s title=%s popup=%s element_id=%s class_name=%s "
                "test_id=%s frame=%s",
                position,
                self._safe_metadata(control.tag),
                self._safe_metadata(control.role),
                self._safe_metadata(control.text),
                self._safe_metadata(control.aria_label),
                self._safe_metadata(control.title),
                self._safe_metadata(control.popup),
                self._safe_metadata(control.element_id),
                self._safe_metadata(control.class_name),
                self._safe_metadata(control.test_id),
                "child" if control.frame_id else "main",
            )
        return len(candidates)

    def click(self, control: PageControl) -> None:
        expression = self._click_expression(control, return_point=control.frame_id is None)
        if control.frame_id is None:
            clicked = self.cdp.evaluate_page(self.target_id, expression)
        else:
            clicked = self.cdp.evaluate_frame(self.target_id, control.frame_id, expression)
        if isinstance(clicked, dict):
            x = clicked.get("x")
            y = clicked.get("y")
            if not isinstance(x, (int, float)) or not isinstance(y, (int, float)):
                clicked = False
            else:
                click_point = getattr(self.cdp, "click_page_point", None)
                if click_point is None:
                    clicked = False
                else:
                    click_point(self.target_id, float(x), float(y))
                    clicked = True
        if clicked is not True:
            raise AssetwayDownloadError(
                "download_control_changed",
                "A opção de download mudou antes de ser selecionada.",
                retryable=True,
            )

    @staticmethod
    def _click_expression(control: PageControl, *, return_point: bool) -> str:
        return rf"""
        (() => {{
          const shortText = value => String(value || "").replace(/\s+/g, " ").trim().slice(0, 160);
          const visible = element => {{
            const style = window.getComputedStyle(element);
            const rect = element.getBoundingClientRect();
            return style.display !== "none" && style.visibility !== "hidden" &&
              Number(style.opacity) !== 0 && rect.width > 0 && rect.height > 0;
          }};
          const selector = {json.dumps(_CONTROL_SELECTOR)};
          const semanticPattern = new RegExp(
            'download|baixar|original|source|fonte|arquivo|file|eps|svg|' +
            'jpeg|jpg|png|alta|high|full|maxim', 'i');
          const interactiveSelector =
            'button,a,input,select,option,[role="button"],[role="option"],' +
            '[role="menuitem"],[role="menuitemradio"],[onclick],[tabindex]';
          const roots = [document];
          for (let cursor = 0; cursor < roots.length; cursor += 1) {{
            for (const node of roots[cursor].querySelectorAll('*')) {{
              if (node.shadowRoot) roots.push(node.shadowRoot);
            }}
          }}
          const label = element => shortText(element.getAttribute('aria-label') ||
            element.innerText || element.textContent || element.value || element.title ||
            element.querySelector('svg')?.getAttribute('aria-label') ||
            element.querySelector('svg title')?.textContent ||
            element.getAttribute('data-action') || element.getAttribute('data-testid'));
          const elements = [];
          const seen = new Set();
          for (const root of roots) {{
            for (const element of root.querySelectorAll(selector)) {{
              if (seen.has(element) || !visible(element)) continue;
              const evidence = [label(element), element.id, element.className,
                element.getAttribute('data-action'), element.getAttribute('data-testid')].join(' ');
              const interactive = element.matches(interactiveSelector);
              const genericEvidence = [element.getAttribute('aria-label'), element.title,
                element.id, element.className, element.getAttribute('data-action'),
                element.getAttribute('data-testid'),
                element.children.length <= 3 && label(element).length <= 80 ?
                  label(element) : ''].join(' ');
              if (!interactive && !semanticPattern.test(genericEvidence)) continue;
              if (!interactive) {{
                const ancestor = element.closest(interactiveSelector);
                if (ancestor && visible(ancestor) &&
                    semanticPattern.test(label(ancestor))) continue;
              }}
              seen.add(element);
              elements.push(element);
            }}
          }}
          let element = elements[{control.index}];
          if ({control.group_index} >= 0) {{
            const compactGroups = Array.from(document.querySelectorAll('.ant-space-compact'));
            element = elements.find(candidate => {{
              const parent = candidate.parentElement;
              const groupIndex = parent?.matches('.ant-space-compact') ?
                compactGroups.indexOf(parent) : -1;
              const siblingIndex = parent ? Array.from(parent.children).indexOf(candidate) : -1;
              const context = candidate.closest(
                '[role="dialog"],.ant-modal,[aria-modal="true"]') ? 'dialog' : 'page';
              return groupIndex === {control.group_index} &&
                siblingIndex === {control.sibling_index} &&
                context === {json.dumps(control.context)};
            }});
          }}
          if (!element || element.disabled || element.getAttribute('aria-disabled') === 'true')
            return false;
          if (label(element) !== {json.dumps(control.display_label)}) return false;
          element.scrollIntoView({{block: 'center', inline: 'center'}});
          const rect = element.getBoundingClientRect();
          if ({str(return_point).lower()})
            return {{x: rect.left + rect.width / 2, y: rect.top + rect.height / 2}};
          element.click();
          return true;
        }})()
        """

    @staticmethod
    def _parse_control(value: object) -> PageControl:
        if not isinstance(value, dict):
            raise AssetwayDownloadError(
                "page_unverified",
                "A página Assetway retornou um controle inválido.",
                retryable=True,
            )
        try:
            return PageControl(
                index=int(value["index"]),
                tag=str(value.get("tag", "")),
                role=str(value.get("role", "")),
                text=str(value.get("text", "")),
                aria_label=str(value.get("ariaLabel", "")),
                title=str(value.get("title", "")),
                popup=str(value.get("popup", "")),
                disabled=bool(value.get("disabled", False)),
                element_id=str(value.get("elementId", "")),
                class_name=str(value.get("className", "")),
                test_id=str(value.get("testId", "")),
                data_action=str(value.get("dataAction", "")),
                data_names=str(value.get("dataNames", "")),
                svg_label=str(value.get("svgLabel", "")),
                context=str(value.get("context", "page")),
                group_index=int(value.get("groupIndex", -1)),
                sibling_index=int(value.get("siblingIndex", -1)),
                icon_only=bool(value.get("iconOnly", False)),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise AssetwayDownloadError(
                "page_unverified",
                "A página Assetway retornou metadados de controle incompletos.",
                retryable=True,
            ) from error

    @staticmethod
    def _safe_count(value: object) -> int:
        return int(value) if isinstance(value, (int, float)) and value >= 0 else 0

    @staticmethod
    def _safe_metadata(value: str) -> str:
        normalized = " ".join(str(value).split())[:160]
        if _SENSITIVE_METADATA.search(normalized):
            return "[redacted]"
        return normalized
