-- Policies are checked in load order: the local allow list first, then Cat-egory.
rpzFile("/etc/cat-egory/allow.rpz", {policyName="allow"})
rpzFile("/var/lib/cat-egory-dns/profile-security.rpz", {policyName="cat-egory"})
