import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).parents[1]


class FrontendXSSTests(unittest.TestCase):
    def _source(self, path):
        return (PROJECT_ROOT / path).read_text(encoding="utf-8")

    def test_user_tables_escape_directory_and_slurm_fields(self):
        source = self._source("templates/users.html")

        for expression in (
            "escapeHtml(user.username)",
            "escapeHtml(user.home)",
            "escapeHtml(user.shell)",
            "escapeHtml(user.groups.join",
            "escapeHtml(job.name",
            "escapeHtml(job.partition",
            "escapeHtml(job.start_time",
        ):
            self.assertIn(expression, source)
        self.assertNotIn("<strong>${user.username}</strong>", source)

    def test_group_and_account_rows_do_not_embed_identifiers_in_handlers(self):
        groups = self._source("templates/groups.html")
        accounts = self._source("templates/accounts.html")

        self.assertIn("loadedGroups[${groupIndex}].name", groups)
        self.assertNotIn("showEditGroupModal('${group.name}')", groups)
        self.assertIn("escapeHtml(group.description", groups)
        self.assertIn("loadedAccounts[${accountIndex}].name", accounts)
        self.assertNotIn("showEditAccountModal('${account.name}')", accounts)
        self.assertIn("escapeHtml(account.organization", accounts)

    def test_association_and_dashboard_rows_escape_backend_text(self):
        associations = self._source("templates/cluster_users.html")
        dashboard = self._source("templates/user_dashboard.html")

        for expression in (
            "escapeHtml(userLabel",
            "escapeHtml(assoc.account",
            "escapeHtml(assoc.cluster",
            "escapeHtml(assoc.partition",
            "loadedAssociations[${assocIndex}]",
        ):
            self.assertIn(expression, associations)
        for expression in (
            "escapeHtml(job.name",
            "escapeHtml(job.partition",
            "escapeHtml(job._time",
            "escapeHtml(partition.name",
        ):
            self.assertIn(expression, dashboard)

    def test_partition_and_node_rows_escape_values_and_use_indexed_actions(self):
        partitions = self._source("static/partitions.js")
        nodes = self._source("static/nodes.js")

        self.assertIn("escapePartitionText(partition.name)", partitions)
        self.assertIn("escapePartitionText(partition.max_time", partitions)
        self.assertIn("loadedPartitions[${partitionIndex}].name", partitions)
        self.assertNotIn("editPartition('${partition.name}')", partitions)
        for expression in (
            "escapePartitionText(node.name)",
            "escapePartitionText(partition.nodes",
            "escapePartitionText(partition.allow_groups",
        ):
            self.assertIn(expression, partitions)
        self.assertIn("escapeNodeText(node.partition", nodes)
        self.assertIn("escapeNodeText(gres)", nodes)
        self.assertIn("escapeNodeText(state)", nodes)
        self.assertIn("escapeNodeText(nodeName)", nodes)
        self.assertIn("escapeNodeText(nodeConfig.gres", nodes)

    def test_confirmation_modal_treats_title_and_message_as_text(self):
        source = self._source("static/main.js")

        self.assertIn("modalTitle.textContent = String(title", source)
        self.assertIn("titleElement.textContent = String(title", source)
        self.assertIn("messageElement.textContent = String(message", source)

    def test_admin_and_dashboard_views_escape_dynamic_values(self):
        admin = self._source("templates/admin.html")
        dashboard = self._source("templates/index.html")

        self.assertIn("|tojson", admin)
        self.assertIn("escapeHtml(username)", admin)
        self.assertIn("loadedAdmins[${idx}]", admin)
        self.assertIn("box.replaceChildren()", admin)
        self.assertIn("item.textContent = u", admin)
        self.assertNotIn("selectSuggestion('${u}')", admin)
        for expression in (
            "escapeHtml(job.user)",
            "escapeHtml(job.partition)",
            "escapeHtml(st.text)",
            "escapeHtml(p.name)",
        ):
            self.assertIn(expression, dashboard)

    def test_current_user_bootstrap_uses_json_encoding(self):
        base = self._source("templates/base.html")

        self.assertIn("user.get('username', '') | tojson", base)
        self.assertIn("user.get('cn', user.get('username', '')) | tojson", base)
        self.assertNotIn("user.get('username', '') | e", base)


if __name__ == "__main__":
    unittest.main()
