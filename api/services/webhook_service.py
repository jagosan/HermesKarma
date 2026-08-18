"""Webhook receiver and bi-directional ticket sync engine for GitHub, Linear, and Jira."""
import hmac
import hashlib
import json
import re
import logging
from typing import Dict, Any, List, Optional, Tuple
from api.services.metadata_service import metadata_service
from api.config import GITHUB_WEBHOOK_SECRET, LINEAR_WEBHOOK_SECRET, JIRA_WEBHOOK_SECRET

logger = logging.getLogger("hermes_karma.webhooks")

# Regex patterns for session ID discovery
SESSION_PATTERNS = [
    re.compile(r'@session:[a-zA-Z0-9_-]+/([a-zA-Z0-9_]+)'),
    re.compile(r'(?<!@)\b(?:session_id|sid|hermes_session)[\s:=]+([a-zA-Z0-9_\-]+)\b', re.IGNORECASE),
    re.compile(r'(?<!@)\bsession[\s:=]+(20\d{6}_\d{6}_[a-f0-9]+|sess_[a-zA-Z0-9_\-]+|[a-zA-Z0-9_-]+_test)\b', re.IGNORECASE),
    re.compile(r'\b(20\d{6}_\d{6}_[a-f0-9]+)\b'),
    re.compile(r'\b(sess_[a-zA-Z0-9_\-]+)\b'),
    re.compile(r'\bhermes[:/](20\d{6}_\d{6}_[a-f0-9]+|sess_[a-zA-Z0-9_\-]+|[a-zA-Z0-9_-]+_test)\b', re.IGNORECASE),
]

IGNORE_SESSION_WORDS = {"default", "main", "master", "karma", "hermes", "session", "agent", "task"}

# Regex patterns for ticket keys
TICKET_KEY_PATTERNS = {
    "github": re.compile(r'(?:^|[\s/])#(\d+)\b'),
    "linear": re.compile(r'\b([A-Z]{2,10}-\d+)\b'),
    "jira": re.compile(r'\b([A-Z][A-Z0-9]+-\d+)\b'),
}


class WebhookService:
    def __init__(self):
        self.github_secret = GITHUB_WEBHOOK_SECRET
        self.linear_secret = LINEAR_WEBHOOK_SECRET
        self.jira_secret = JIRA_WEBHOOK_SECRET

    def verify_github_signature(self, raw_body: bytes, signature_header: Optional[str]) -> bool:
        if not self.github_secret:
            return True  # If no secret configured, allow (e.g. dev/local homelab)
        if not signature_header:
            return False
        
        parts = signature_header.split("sha256=")
        if len(parts) != 2:
            return False
        expected_sig = parts[1]
        
        computed_sig = hmac.new(
            self.github_secret.encode("utf-8"),
            raw_body,
            hashlib.sha256,
        ).hexdigest()
        
        return hmac.compare_digest(expected_sig, computed_sig)

    def verify_linear_signature(self, raw_body: bytes, signature_header: Optional[str]) -> bool:
        if not self.linear_secret:
            return True
        if not signature_header:
            return False
        
        computed_sig = hmac.new(
            self.linear_secret.encode("utf-8"),
            raw_body,
            hashlib.sha256,
        ).hexdigest()
        
        return hmac.compare_digest(signature_header, computed_sig)

    def verify_jira_signature(self, token_header_or_param: Optional[str]) -> bool:
        if not self.jira_secret:
            return True
        if not token_header_or_param:
            return False
        return hmac.compare_digest(self.jira_secret, token_header_or_param)

    def extract_session_ids(self, *texts: Optional[str]) -> List[str]:
        """Extract referenced Hermes session IDs from bodies, comments, titles, or branch names."""
        found = set()
        for text in texts:
            if not text:
                continue
            for pat in SESSION_PATTERNS:
                for match in pat.findall(text):
                    clean = match.strip()
                    if clean and clean.lower() not in IGNORE_SESSION_WORDS:
                        found.add(clean)
        return sorted(list(found))

    def extract_ticket_keys(self, provider: str, *texts: Optional[str]) -> List[str]:
        """Extract ticket keys from text."""
        pat = TICKET_KEY_PATTERNS.get(provider.lower())
        if not pat:
            return []
        found = set()
        for text in texts:
            if not text:
                continue
            for match in pat.findall(text):
                if provider.lower() == "github" and not match.startswith("#"):
                    found.add(f"#{match}")
                else:
                    found.add(match)
        return sorted(list(found))

    def handle_github_webhook(
        self,
        event_type: str,
        payload: Dict[str, Any],
        delivery_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Process incoming GitHub webhook (issues, issue_comment, pull_request, push)."""
        ticket_key = None
        title = None
        url = None
        status = "open"
        assignee = None
        description = None
        associated_sessions: List[str] = []

        if "issue" in payload:
            issue = payload["issue"]
            ticket_key = f"#{issue.get('number')}"
            title = issue.get("title")
            url = issue.get("html_url")
            status = issue.get("state", "open")
            if issue.get("assignee"):
                assignee = issue["assignee"].get("login")
            description = issue.get("body") or ""
            
            comment_body = payload.get("comment", {}).get("body", "")
            associated_sessions = self.extract_session_ids(description, comment_body, title)

        elif "pull_request" in payload:
            pr = payload["pull_request"]
            ticket_key = f"#{pr.get('number')}"
            title = pr.get("title")
            url = pr.get("html_url")
            status = "merged" if pr.get("merged") else pr.get("state", "open")
            if pr.get("assignee"):
                assignee = pr["assignee"].get("login")
            description = pr.get("body") or ""
            head_ref = pr.get("head", {}).get("ref", "")
            
            comment_body = payload.get("comment", {}).get("body", "")
            associated_sessions = self.extract_session_ids(description, comment_body, title, head_ref)

        elif "push" in event_type or "commits" in payload:
            ref = payload.get("ref", "")
            commits = payload.get("commits", [])
            messages = " ".join([c.get("message", "") for c in commits])
            associated_sessions = self.extract_session_ids(ref, messages)
            extracted_tickets = self.extract_ticket_keys("github", ref, messages)
            if extracted_tickets:
                ticket_key = extracted_tickets[0]
            title = f"Push on {ref}"
            url = payload.get("compare")

        # Fallback explicit session_id in payload if passed
        if payload.get("session_id") and payload["session_id"] not in associated_sessions:
            associated_sessions.append(payload["session_id"])

        synced_count = 0
        if ticket_key:
            # If sessions found, link them
            for sid in associated_sessions:
                metadata_service.add_ticket_link(
                    session_id=sid,
                    provider="github",
                    ticket_key=ticket_key,
                    url=url,
                    title=title,
                    status=status,
                    assignee=assignee,
                    description=description,
                    raw_data=payload,
                )
                synced_count += 1
            
            # Also update existing ticket rows with newest status/url/title
            metadata_service.update_ticket_status_by_key(
                provider="github",
                ticket_key=ticket_key,
                status=status,
                title=title,
                url=url,
                assignee=assignee,
                raw_data=payload,
            )

        # Log webhook event
        event_id = metadata_service.log_webhook_event(
            provider="github",
            event_type=event_type,
            delivery_id=delivery_id,
            ticket_key=ticket_key,
            payload=payload,
            status="processed" if ticket_key or associated_sessions else "ignored",
            linked_sessions=associated_sessions,
        )

        return {
            "success": True,
            "event_id": event_id,
            "provider": "github",
            "event_type": event_type,
            "ticket_key": ticket_key,
            "status": status,
            "linked_sessions": associated_sessions,
            "synced_count": synced_count,
        }

    def handle_linear_webhook(
        self,
        payload: Dict[str, Any],
        delivery_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Process incoming Linear webhook (Issue, Comment, etc.)."""
        action = payload.get("action", "unknown")
        event_type = f"linear:{payload.get('type', 'Issue')}.{action}"
        data = payload.get("data", {})
        
        ticket_key = data.get("identifier") or data.get("key")
        title = data.get("title")
        url = data.get("url")
        description = data.get("description") or ""
        
        state_obj = data.get("state", {})
        status = state_obj.get("name") if isinstance(state_obj, dict) else (data.get("state") or "open")
        
        assignee_obj = data.get("assignee", {})
        assignee = assignee_obj.get("name") if isinstance(assignee_obj, dict) else None

        # Comment event handling
        comment_body = ""
        if payload.get("type") == "Comment":
            comment_body = data.get("body", "")
            issue_info = data.get("issue", {})
            if isinstance(issue_info, dict):
                ticket_key = issue_info.get("identifier") or ticket_key
                title = issue_info.get("title") or title
                url = issue_info.get("url") or url

        # Extract session IDs
        associated_sessions = self.extract_session_ids(
            description,
            comment_body,
            title,
            data.get("branchName", ""),
        )

        if payload.get("session_id") and payload["session_id"] not in associated_sessions:
            associated_sessions.append(payload["session_id"])

        synced_count = 0
        if ticket_key:
            for sid in associated_sessions:
                metadata_service.add_ticket_link(
                    session_id=sid,
                    provider="linear",
                    ticket_key=ticket_key,
                    url=url,
                    title=title,
                    status=status,
                    assignee=assignee,
                    description=description or comment_body,
                    raw_data=payload,
                )
                synced_count += 1

            metadata_service.update_ticket_status_by_key(
                provider="linear",
                ticket_key=ticket_key,
                status=status,
                title=title,
                url=url,
                assignee=assignee,
                raw_data=payload,
            )

        event_id = metadata_service.log_webhook_event(
            provider="linear",
            event_type=event_type,
            delivery_id=delivery_id or payload.get("id"),
            ticket_key=ticket_key,
            payload=payload,
            status="processed" if ticket_key or associated_sessions else "ignored",
            linked_sessions=associated_sessions,
        )

        return {
            "success": True,
            "event_id": event_id,
            "provider": "linear",
            "event_type": event_type,
            "ticket_key": ticket_key,
            "status": status,
            "linked_sessions": associated_sessions,
            "synced_count": synced_count,
        }

    def handle_jira_webhook(
        self,
        payload: Dict[str, Any],
        delivery_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Process incoming Jira webhook (jira:issue_created, jira:issue_updated, etc.)."""
        event_type = payload.get("webhookEvent", "jira:issue_event")
        issue = payload.get("issue", {})
        fields = issue.get("fields", {}) if isinstance(issue, dict) else {}

        ticket_key = issue.get("key") if isinstance(issue, dict) else payload.get("issue_key")
        title = fields.get("summary") or payload.get("summary")
        
        self_url = issue.get("self", "")
        # Convert API self URL to UI URL if possible
        if self_url and "/rest/api/" in self_url:
            url = self_url.split("/rest/api/")[0] + f"/browse/{ticket_key}"
        else:
            url = self_url or payload.get("url")

        status_obj = fields.get("status", {})
        status = status_obj.get("name") if isinstance(status_obj, dict) else (fields.get("status") or "open")

        assignee_obj = fields.get("assignee", {})
        assignee = assignee_obj.get("displayName") or assignee_obj.get("name") if isinstance(assignee_obj, dict) else None

        description = fields.get("description") or ""
        comment_body = payload.get("comment", {}).get("body", "")

        associated_sessions = self.extract_session_ids(str(description), str(comment_body), str(title))
        if payload.get("session_id") and payload["session_id"] not in associated_sessions:
            associated_sessions.append(payload["session_id"])

        synced_count = 0
        if ticket_key:
            for sid in associated_sessions:
                metadata_service.add_ticket_link(
                    session_id=sid,
                    provider="jira",
                    ticket_key=ticket_key,
                    url=url,
                    title=title,
                    status=status,
                    assignee=assignee,
                    description=str(description) if description else None,
                    raw_data=payload,
                )
                synced_count += 1

            metadata_service.update_ticket_status_by_key(
                provider="jira",
                ticket_key=ticket_key,
                status=status,
                title=title,
                url=url,
                assignee=assignee,
                raw_data=payload,
            )

        event_id = metadata_service.log_webhook_event(
            provider="jira",
            event_type=event_type,
            delivery_id=delivery_id,
            ticket_key=ticket_key,
            payload=payload,
            status="processed" if ticket_key or associated_sessions else "ignored",
            linked_sessions=associated_sessions,
        )

        return {
            "success": True,
            "event_id": event_id,
            "provider": "jira",
            "event_type": event_type,
            "ticket_key": ticket_key,
            "status": status,
            "linked_sessions": associated_sessions,
            "synced_count": synced_count,
        }

    def handle_generic_webhook(
        self,
        provider: str,
        payload: Dict[str, Any],
        delivery_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Generic webhook receiver for custom providers or manual sync dispatches."""
        ticket_key = payload.get("ticket_key") or payload.get("key") or payload.get("issue_id")
        title = payload.get("title") or payload.get("summary")
        url = payload.get("url")
        status = payload.get("status", "open")
        assignee = payload.get("assignee")
        description = payload.get("description") or payload.get("body")
        
        associated_sessions = self.extract_session_ids(
            str(description or ""),
            str(title or ""),
            str(payload.get("notes", "")),
        )
        if payload.get("session_id") and payload["session_id"] not in associated_sessions:
            associated_sessions.append(payload["session_id"])

        synced_count = 0
        if ticket_key:
            for sid in associated_sessions:
                metadata_service.add_ticket_link(
                    session_id=sid,
                    provider=provider,
                    ticket_key=ticket_key,
                    url=url,
                    title=title,
                    status=status,
                    assignee=assignee,
                    description=description,
                    raw_data=payload,
                )
                synced_count += 1

            metadata_service.update_ticket_status_by_key(
                provider=provider,
                ticket_key=ticket_key,
                status=status,
                title=title,
                url=url,
                assignee=assignee,
                raw_data=payload,
            )

        event_id = metadata_service.log_webhook_event(
            provider=provider,
            event_type="generic:sync",
            delivery_id=delivery_id,
            ticket_key=ticket_key,
            payload=payload,
            status="processed" if ticket_key or associated_sessions else "ignored",
            linked_sessions=associated_sessions,
        )

        return {
            "success": True,
            "event_id": event_id,
            "provider": provider,
            "event_type": "generic:sync",
            "ticket_key": ticket_key,
            "status": status,
            "linked_sessions": associated_sessions,
            "synced_count": synced_count,
        }


webhook_service = WebhookService()
