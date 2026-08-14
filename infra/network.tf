# --- egress control: default-deny VPC the sandbox is forced through ---
# All resources exist only under egress_control = true. The runner (and
# scheduler) attach via direct VPC egress with egress = ALL_TRAFFIC, so every
# packet — Google APIs included — crosses this VPC and is subject to the
# firewall policy below. Google APIs stay reachable without touching the
# internet via Private Google Access: a private googleapis.com zone maps
# *.googleapis.com onto the private.googleapis.com VIP, which one IP allow
# rule admits. Everything else is allowed only by FQDN, then NATed out.
#
# Honest caveat: FQDN rules are DNS-resolution based (the dataplane admits
# the IPs the allowed names resolve to); they don't inspect TLS SNI, so a
# host sharing an allowed domain's IPs is not blocked. For SNI-level
# enforcement, front the VPC with Secure Web Proxy instead.

resource "google_compute_network" "egress" {
  count                   = var.egress_control ? 1 : 0
  name                    = "syros-egress"
  auto_create_subnetworks = false
  depends_on              = [google_project_service.apis]
}

resource "google_compute_subnetwork" "runner" {
  count                    = var.egress_control ? 1 : 0
  name                     = "syros-runner"
  region                   = var.region # must match the job's region
  network                  = google_compute_network.egress[0].id
  ip_cidr_range            = "10.10.0.0/24" # direct VPC egress consumes IPs per instance
  private_ip_google_access = true
}

# NAT is the external path for the FQDN-allowed domains; PGA traffic never
# reaches it.
resource "google_compute_router" "egress" {
  count   = var.egress_control ? 1 : 0
  name    = "syros-egress"
  region  = var.region
  network = google_compute_network.egress[0].id
}

resource "google_compute_router_nat" "egress" {
  count                              = var.egress_control ? 1 : 0
  name                               = "syros-egress"
  region                             = var.region
  router                             = google_compute_router.egress[0].name
  nat_ip_allocate_option             = "AUTO_ONLY"
  source_subnetwork_ip_ranges_to_nat = "ALL_SUBNETWORKS_ALL_IP_RANGES"
}

# Private Google Access: FQDN objects don't support wildcards, so
# *.googleapis.com is steered onto the fixed private VIP with a private DNS
# zone instead, and the firewall admits the VIP range.
resource "google_dns_managed_zone" "googleapis" {
  count      = var.egress_control ? 1 : 0
  name       = "syros-googleapis"
  dns_name   = "googleapis.com."
  visibility = "private"

  private_visibility_config {
    networks {
      network_url = google_compute_network.egress[0].id
    }
  }
}

resource "google_dns_record_set" "private_googleapis" {
  count        = var.egress_control ? 1 : 0
  managed_zone = google_dns_managed_zone.googleapis[0].name
  name         = "private.googleapis.com."
  type         = "A"
  ttl          = 300
  rrdatas      = ["199.36.153.8", "199.36.153.9", "199.36.153.10", "199.36.153.11"]
}

resource "google_dns_record_set" "googleapis_wildcard" {
  count        = var.egress_control ? 1 : 0
  managed_zone = google_dns_managed_zone.googleapis[0].name
  name         = "*.googleapis.com."
  type         = "CNAME"
  ttl          = 300
  rrdatas      = ["private.googleapis.com."]
}

# FQDN egress rules exist only in network firewall policies (not legacy
# google_compute_firewall). Global: one VPC, nothing region-specific.
resource "google_compute_network_firewall_policy" "egress" {
  count       = var.egress_control ? 1 : 0
  name        = "syros-egress"
  description = "Default-deny egress for the syros sandbox; FQDN allowlist plus Private Google Access"
}

resource "google_compute_network_firewall_policy_association" "egress" {
  count             = var.egress_control ? 1 : 0
  name              = "syros-egress"
  firewall_policy   = google_compute_network_firewall_policy.egress[0].name
  attachment_target = google_compute_network.egress[0].id
}

resource "google_compute_network_firewall_policy_rule" "allow_domains" {
  count           = var.egress_control ? 1 : 0
  firewall_policy = google_compute_network_firewall_policy.egress[0].name
  description     = "The operator's domain allowlist"
  priority        = 100
  direction       = "EGRESS"
  action          = "allow"

  match {
    dest_fqdns = var.allowed_egress_domains
    layer4_configs {
      ip_protocol = "tcp"
      ports       = ["443"]
    }
  }
}

resource "google_compute_network_firewall_policy_rule" "allow_private_googleapis" {
  count           = var.egress_control ? 1 : 0
  firewall_policy = google_compute_network_firewall_policy.egress[0].name
  description     = "Private Google Access VIP (Vertex, Firestore, GCS, Secret Manager, *.mcp.googleapis.com)"
  priority        = 200
  direction       = "EGRESS"
  action          = "allow"

  match {
    dest_ip_ranges = ["199.36.153.8/30"]
    layer4_configs {
      ip_protocol = "tcp"
      ports       = ["443"]
    }
  }
}

# Cloud DNS forwarding range — defensive: direct-VPC-egress workloads usually
# resolve via the metadata server (not firewall-subject), but FQDN rules
# depend on DNS answers being observable, so keep the dataplane path open.
resource "google_compute_network_firewall_policy_rule" "allow_dns" {
  count           = var.egress_control ? 1 : 0
  firewall_policy = google_compute_network_firewall_policy.egress[0].name
  description     = "Cloud DNS"
  priority        = 300
  direction       = "EGRESS"
  action          = "allow"

  match {
    dest_ip_ranges = ["35.199.192.0/19"]
    layer4_configs {
      ip_protocol = "tcp"
      ports       = ["53"]
    }
    layer4_configs {
      ip_protocol = "udp"
      ports       = ["53"]
    }
  }
}

resource "google_compute_network_firewall_policy_rule" "deny_all" {
  count           = var.egress_control ? 1 : 0
  firewall_policy = google_compute_network_firewall_policy.egress[0].name
  description     = "Default deny: everything not explicitly allowed above"
  priority        = 65000
  direction       = "EGRESS"
  action          = "deny"

  match {
    dest_ip_ranges = ["0.0.0.0/0"]
    layer4_configs {
      ip_protocol = "all"
    }
  }
}
