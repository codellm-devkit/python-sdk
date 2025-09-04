################################################################################
# Copyright IBM Corporation 2024
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#       http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
################################################################################

"""Sanitize Java classes around a focal method using tree-sitter.

This module provides utilities to prune Java source code by keeping only a
focal test/method and its transitive callees, and by removing unused fields,
imports, and inner classes.
"""

import logging
from copy import deepcopy
from typing import Dict, List, Set

from cldk.analysis.commons.treesitter import TreesitterJava
from cldk.analysis.commons.treesitter.models import Captures

log = logging.getLogger(__name__)


class TreesitterSanitizer:
    """Sanitize Java source code using tree-sitter queries.

    The sanitizer focuses a class to a given focal method by keeping that
    method and its callees, then removes unused fields, imports, and inner
    classes. It also strips block comments before processing.
    """

    def __init__(self, source_code):
        """Initialize the sanitizer.

        Args:
            source_code (str): The full Java source code to sanitize.
        """
        self.source_code = source_code
        self.sanitized_code = deepcopy(self.source_code)
        self.__javasitter = TreesitterJava()

    def keep_only_focal_method_and_its_callees(self, focal_method: str) -> str:
        """Remove all methods except the focal method and its callees.

        Args:
            focal_method (str): The name of the focal method.

        Returns:
            str: The pruned source code.

        Examples:
            >>> src = 'class A { void keep(){} void drop(){} }'
            >>> tz = TreesitterSanitizer(src)
            >>> out = tz.keep_only_focal_method_and_its_callees('keep')
            >>> 'drop' in out
            False
        """
        method_declaration: Captures = self.__javasitter.frame_query_and_capture_output(query="((method_declaration) " "@method_declaration)", code_to_process=self.sanitized_code)
        declared_methods = {self.__javasitter.get_method_name_from_declaration(capture.node.text.decode()): capture.node.text.decode() for capture in method_declaration}
        unused_methods: Dict = self._unused_methods(focal_method, declared_methods)
        for _, method_body in unused_methods.items():
            self.sanitized_code = self.sanitized_code.replace(method_body, "")
        return self.__javasitter.make_pruned_code_prettier(self.sanitized_code)

    def remove_unused_imports(self, sanitized_code: str) -> str:
        """Remove imports not referenced in the class body.

        Assumes fields have already been pruned. Scans type identifiers and
        other identifiers to decide which imports are used. Wildcard imports
        are preserved.

        Args:
            sanitized_code (str): Source code after earlier pruning steps.

        Returns:
            str: The pruned source code with unused imports removed.

        Notes:
            - Compare the last segment of each import against collected
              identifiers and type identifiers.
            - Keep wildcard imports (ending with '*').
        Examples:
            >>> src = 'import java.util.List; class A { }'
            >>> TreesitterSanitizer(src).remove_unused_imports(src)
            ''
        """
        pruned_source_code: str = deepcopy(sanitized_code)
        import_declarations: Captures = self.__javasitter.frame_query_and_capture_output(query="((import_declaration) @imports)", code_to_process=self.source_code)

        unused_imports: Set = set()
        ids_and_typeids: Set = set()
        class_bodies: Captures = self.__javasitter.frame_query_and_capture_output(query="((class_declaration) @class_declaration)", code_to_process=self.source_code)
        for class_body in class_bodies:
            all_type_identifiers_in_class: Captures = self.__javasitter.frame_query_and_capture_output(
                query="((type_identifier) @type_id)",
                code_to_process=class_body.node.text.decode(),
            )
            all_other_identifiers_in_class: Captures = self.__javasitter.frame_query_and_capture_output(
                query="((identifier) @other_id)",
                code_to_process=class_body.node.text.decode(),
            )
            ids_and_typeids.update({type_id.node.text.decode() for type_id in all_type_identifiers_in_class})
            ids_and_typeids.update({other_id.node.text.decode() for other_id in all_other_identifiers_in_class})

        for import_declaration in import_declarations:
            wildcard_import: Captures = self.__javasitter.frame_query_and_capture_output(query="((asterisk) @wildcard)", code_to_process=import_declaration.node.text.decode())
            if len(wildcard_import) > 0:
                continue

            import_statement: Captures = self.__javasitter.frame_query_and_capture_output(
                query="((scoped_identifier) @scoped_identifier)", code_to_process=import_declaration.node.text.decode()
            )
            try:
                import_str = import_statement.captures[0].node.text.decode()
            except IndexError:
                continue
            if import_str.split(".")[-1] not in ids_and_typeids:
                unused_imports.add(import_declaration.node.text.decode())

        for unused_import in unused_imports:
            pruned_source_code = pruned_source_code.replace(unused_import, "")

        return self.__javasitter.make_pruned_code_prettier(pruned_source_code)

    def remove_unused_fields(self, sanitized_code: str) -> str:
        """Remove fields not referenced in any method or constructor.

        Args:
            sanitized_code (str): Source after removing unused methods.

        Returns:
            str: Source with unused fields removed.

        Notes:
            - Collect identifiers used in all methods and constructors, then
              drop field declarations whose identifiers don't appear.
        Examples:
            >>> src = 'class A { int x; void f(){ int y = 1; } }'
            >>> out = TreesitterSanitizer(src).remove_unused_fields(src)
            >>> 'int x;' in out
            False
        """
        pruned_source_code: str = deepcopy(sanitized_code)
        unused_fields: List[Captures.Capture] = list()
        field_declarations: Captures = self.__javasitter.frame_query_and_capture_output(query="((field_declaration) @field_declaration)", code_to_process=pruned_source_code)
        method_declaration: Captures = self.__javasitter.frame_query_and_capture_output(query="((method_declaration) @method_declaration)", code_to_process=pruned_source_code)
        constructor_declaration: Captures = self.__javasitter.frame_query_and_capture_output(
            query="((constructor_declaration) @constructor_declaration)", code_to_process=pruned_source_code
        )
        all_used_identifiers = set()
        for method in method_declaration:
            all_used_identifiers.update(
                {
                    capture.node.text.decode()
                    for capture in self.__javasitter.frame_query_and_capture_output(query="((identifier) @identifier)", code_to_process=method.node.text.decode())
                }
            )

        for constructor in constructor_declaration:
            all_used_identifiers.update(
                {
                    capture.node.text.decode()
                    for capture in self.__javasitter.frame_query_and_capture_output(query="((identifier) @identifier)", code_to_process=constructor.node.text.decode())
                }
            )

        used_fields = [capture for capture in field_declarations]

        for field in used_fields:
            field_identifiers = {
                capture.node.text.decode()
                for capture in self.__javasitter.frame_query_and_capture_output(query="((identifier) @identifier)", code_to_process=field.node.text.decode())
            }
            if not field_identifiers.intersection(all_used_identifiers):
                unused_fields.append(field)

        for unused_field in unused_fields:
            pruned_source_code = pruned_source_code.replace(unused_field.node.text.decode(), "")

        return self.__javasitter.make_pruned_code_prettier(pruned_source_code)

    def remove_unused_classes(self, sanitized_code: str) -> str:
        """Remove unused inner classes.

        Args:
            sanitized_code (str): The sanitized code to process.

        Returns:
            str: The pruned source code with unused inner classes removed.

        Notes:
            - Start from the outermost class, traverse type invocations, and
              keep only reachable inner classes.
        Examples:
            >>> src = 'class A { class B{} }'
            >>> out = TreesitterSanitizer(src).remove_unused_classes(src)
            >>> 'class B' in out
            False
        """
        focal_class = self.__javasitter.frame_query_and_capture_output(query="(class_declaration name: (identifier) @name)", code_to_process=self.source_code)

        try:
            # We use [0] because there may be several nested classes,
            # we'll consider the outermost class as the focal class.
            focal_class_name = focal_class[0].node.text.decode()
        except Exception:
            return ""

        pruned_source_code = deepcopy(sanitized_code)

        # Find the first class and we'll continue to operate on the inner classes.
        inner_class_declarations: Captures = self.__javasitter.frame_query_and_capture_output("((class_declaration) @class_declaration)", pruned_source_code)

        # Store a dictionary of all the inner classes.
        all_classes = dict()
        for capture in inner_class_declarations:
            inner_class = self.__javasitter.frame_query_and_capture_output(query="(class_declaration name: (identifier) @name)", code_to_process=capture.node.text.decode())
            all_classes[inner_class[0].node.text.decode()] = capture.node.text.decode()

        unused_classes: dict = deepcopy(all_classes)

        to_process = {focal_class_name}

        processed_so_far: Set = set()

        while to_process:
            current_class_name = to_process.pop()
            current_class_body = unused_classes.pop(current_class_name)
            current_class_without_inner_class = current_class_body
            processed_so_far.add(current_class_name)

            # Remove the body of inner classes from the current outer class.
            inner_class_declarations: Captures = self.__javasitter.frame_query_and_capture_output("(class_body (class_declaration) @class_declaration)", current_class_body)
            for capture in inner_class_declarations:
                current_class_without_inner_class = current_class_without_inner_class.replace(capture.node.text.decode(), "")

            # Find all the type_references in the current class.
            type_references: Set[str] = self.__javasitter.get_all_type_invocations(current_class_without_inner_class)
            to_process.update({type_reference for type_reference in type_references if type_reference in all_classes and type_reference not in processed_so_far})

        for _, unused_class_body in unused_classes.items():
            pruned_source_code = pruned_source_code.replace(unused_class_body, "")

        return self.__javasitter.make_pruned_code_prettier(pruned_source_code)

    def _unused_methods(self, focal_method: str, declared_methods: Dict) -> Dict:
        """Compute methods unused given a focal method.

        Args:
            focal_method (str): Starting method name; all others are candidates for removal.
            declared_methods (dict): Map of method name to its body text.

        Returns:
            dict[str, str]: Unused methods with their bodies.

        Notes:
            - Traverse the call graph from the focal method using in-class call
              targets; anything unreached is unused.
        """

        unused_methods = deepcopy(declared_methods)  # A deep copy of unused methods.

        # A stack to hold the methods to process.
        to_process = [focal_method]  # Remove this element from unused methods and put it
        # in the to_process stack.

        # The set below holds all processed methods bodies. This helps avoid recursive and cyclical calls.
        processed_so_far: Set = set()

        while to_process:
            # Remove the current method from the to process stack
            current_method_name = to_process.pop()

            # This method has been processed already, so we'll skip it.
            if current_method_name in processed_so_far:
                continue
            current_method_body = unused_methods.pop(current_method_name)
            processed_so_far.add(current_method_name)
            # Below, we find all method invocations that are made inside current_method_body that are also declared in
            # the class. We will get back an empty set if there are no more.
            all_invoked_methods = self.__javasitter.get_call_targets(current_method_body, declared_methods=declared_methods)
            # Add all the methods invoked in a call to to_process iff those methods are declared in the class.
            to_process.extend([invoked_method_name for invoked_method_name in all_invoked_methods if invoked_method_name not in processed_so_far])

        assert len(unused_methods) < len(declared_methods), "At least one of the declared methods (the focal method) must have be used?"

        return unused_methods

    def sanitize_focal_class(self, focal_method: str) -> str:
        """Produce sanitized source focused on a focal method.

        Args:
            focal_method (str): The focal method declaration text or name.

        Returns:
            str: Pruned source code with only relevant members retained.

        Examples:
            >>> src = 'class A { void keep(){} void drop(){} }'
            >>> TreesitterSanitizer(src).sanitize_focal_class('keep')
            'class A { void keep(){}  }'
        """

        focal_method_name = self.__javasitter.get_method_name_from_declaration(focal_method)

        # Remove block comments
        sanitized_code = self.__javasitter.remove_all_comments(self.sanitized_code)

        # The source code after removing
        sanitized_code = self.keep_only_focal_method_and_its_callees(focal_method_name)

        # Focal method was found in the class, remove unused fields, imports, and classes.
        sanitized_code = self.remove_unused_fields(sanitized_code)

        # Focal method was found in the class, remove unused fields, imports, and classes.
        sanitized_code = self.remove_unused_imports(sanitized_code)

        # Focal method was found in the class, remove unused fields, imports, and classes.
        sanitized_code = self.remove_unused_classes(sanitized_code)

        return sanitized_code
