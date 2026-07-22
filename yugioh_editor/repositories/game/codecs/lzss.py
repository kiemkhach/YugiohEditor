from __future__ import annotations

from yugioh_editor.common.errors import InvalidFileFormatError


class PowerOfChaosLzssCodec:
    """Okumura LZSS codec used by Yu-Gi-Oh! Power of Chaos containers.

    - 4096-byte zero-filled ring buffer;
    - 18-byte maximum match length;
    - minimum encoded match length of 3 bytes;
    - ring cursor starts at ``4096 - 18`` (``0xFEE``);
    - one flag byte controls eight tokens, least-significant bit first;
    - flag bit 1 means literal, flag bit 0 means a two-byte back-reference.
    """

    WINDOW_SIZE = 4096
    LOOK_AHEAD_SIZE = 18
    THRESHOLD = 2
    NIL = WINDOW_SIZE
    CURSOR_START = WINDOW_SIZE - LOOK_AHEAD_SIZE

    def decompress(self, source: bytes, expected_size: int | None = None) -> bytes:
        if source is None:
            raise TypeError("source cannot be None")
        if not source:
            if expected_size not in (None, 0):
                raise InvalidFileFormatError(
                    f"LZSS output size mismatch: expected {expected_size}, got 0."
                )
            return b""

        window = bytearray(self.WINDOW_SIZE)
        cursor = self.CURSOR_START
        output = bytearray()
        source_position = 0

        while source_position < len(source):
            flags = source[source_position]
            source_position += 1

            for bit in range(8):
                if source_position >= len(source):
                    break

                is_literal = ((flags >> bit) & 1) == 1
                if is_literal:
                    value = source[source_position]
                    source_position += 1
                    output.append(value)
                    window[cursor] = value
                    cursor = (cursor + 1) & (self.WINDOW_SIZE - 1)
                    continue

                if source_position + 1 >= len(source):
                    break

                first = source[source_position]
                second = source[source_position + 1]
                source_position += 2

                offset = first | ((second & 0xF0) << 4)
                length = (second & 0x0F) + (self.THRESHOLD + 1)

                for index in range(length):
                    value = window[(offset + index) & (self.WINDOW_SIZE - 1)]
                    output.append(value)
                    window[cursor] = value
                    cursor = (cursor + 1) & (self.WINDOW_SIZE - 1)

        if expected_size is not None and len(output) != expected_size:
            raise InvalidFileFormatError(
                f"LZSS output size mismatch: expected {expected_size}, "
                f"got {len(output)}."
            )

        return bytes(output)

    def compress(self, source: bytes) -> bytes:
        if source is None:
            raise TypeError("source cannot be None")
        if not source:
            return b""

        text_buffer = bytearray(self.WINDOW_SIZE + self.LOOK_AHEAD_SIZE - 1)
        left_children = [self.NIL] * (self.WINDOW_SIZE + 1)
        right_children = [self.NIL] * (self.WINDOW_SIZE + 257)
        parents = [self.NIL] * (self.WINDOW_SIZE + 1)

        search_position = 0
        look_ahead_position = self.CURSOR_START
        source_position = 0
        look_ahead_length = min(self.LOOK_AHEAD_SIZE, len(source))

        text_buffer[look_ahead_position : look_ahead_position + look_ahead_length] = (
            source[:look_ahead_length]
        )
        source_position = look_ahead_length

        match_position = 0
        match_length = 0

        for index in range(1, self.LOOK_AHEAD_SIZE + 1):
            match_position, match_length = self._insert_node(
                look_ahead_position - index,
                text_buffer,
                left_children,
                right_children,
                parents,
            )

        match_position, match_length = self._insert_node(
            look_ahead_position,
            text_buffer,
            left_children,
            right_children,
            parents,
        )

        output = bytearray()
        code_buffer = bytearray(1 + 2 * 8)
        code_pointer = 1
        mask = 1

        while True:
            match_length = min(match_length, look_ahead_length)

            if match_length <= self.THRESHOLD:
                match_length = 1
                code_buffer[0] |= mask
                code_buffer[code_pointer] = text_buffer[look_ahead_position]
                code_pointer += 1
            else:
                code_buffer[code_pointer] = match_position & 0xFF
                code_buffer[code_pointer + 1] = ((match_position >> 4) & 0xF0) | (
                    match_length - (self.THRESHOLD + 1)
                )
                code_pointer += 2

            mask <<= 1
            if mask == 0x100:
                output.extend(code_buffer[:code_pointer])
                code_buffer[0] = 0
                code_pointer = 1
                mask = 1

            last_match_length = match_length
            moved = 0

            while moved < last_match_length and source_position < len(source):
                value = source[source_position]
                source_position += 1

                self._delete_node(
                    search_position,
                    left_children,
                    right_children,
                    parents,
                )

                text_buffer[search_position] = value
                if search_position < self.LOOK_AHEAD_SIZE - 1:
                    text_buffer[search_position + self.WINDOW_SIZE] = value

                search_position = (search_position + 1) & (self.WINDOW_SIZE - 1)
                look_ahead_position = (look_ahead_position + 1) & (self.WINDOW_SIZE - 1)

                match_position, match_length = self._insert_node(
                    look_ahead_position,
                    text_buffer,
                    left_children,
                    right_children,
                    parents,
                )
                moved += 1

            while moved < last_match_length:
                self._delete_node(
                    search_position,
                    left_children,
                    right_children,
                    parents,
                )

                search_position = (search_position + 1) & (self.WINDOW_SIZE - 1)
                look_ahead_position = (look_ahead_position + 1) & (self.WINDOW_SIZE - 1)

                look_ahead_length -= 1
                if look_ahead_length > 0:
                    match_position, match_length = self._insert_node(
                        look_ahead_position,
                        text_buffer,
                        left_children,
                        right_children,
                        parents,
                    )
                moved += 1

            if look_ahead_length <= 0:
                break

        if code_pointer > 1:
            output.extend(code_buffer[:code_pointer])

        return bytes(output)

    def _insert_node(
        self,
        node: int,
        text_buffer: bytearray,
        left_children: list[int],
        right_children: list[int],
        parents: list[int],
    ) -> tuple[int, int]:
        comparison = 1
        parent = self.WINDOW_SIZE + 1 + text_buffer[node]
        right_children[node] = self.NIL
        left_children[node] = self.NIL
        match_length = 0
        match_position = 0

        while True:
            if comparison >= 0:
                if right_children[parent] != self.NIL:
                    parent = right_children[parent]
                else:
                    right_children[parent] = node
                    parents[node] = parent
                    return match_position, match_length
            else:
                if left_children[parent] != self.NIL:
                    parent = left_children[parent]
                else:
                    left_children[parent] = node
                    parents[node] = parent
                    return match_position, match_length

            index = 1
            while index < self.LOOK_AHEAD_SIZE:
                comparison = (
                    text_buffer[(node + index) & (self.WINDOW_SIZE - 1)]
                    - text_buffer[(parent + index) & (self.WINDOW_SIZE - 1)]
                )
                if comparison != 0:
                    break
                index += 1

            if index > match_length:
                match_position = parent
                match_length = index
                if match_length >= self.LOOK_AHEAD_SIZE:
                    break

        parents[node] = parents[parent]
        left_children[node] = left_children[parent]
        right_children[node] = right_children[parent]

        parents[left_children[parent]] = node
        parents[right_children[parent]] = node

        if right_children[parents[parent]] == parent:
            right_children[parents[parent]] = node
        else:
            left_children[parents[parent]] = node

        parents[parent] = self.NIL
        return match_position, match_length

    def _delete_node(
        self,
        node: int,
        left_children: list[int],
        right_children: list[int],
        parents: list[int],
    ) -> None:
        if parents[node] == self.NIL:
            return

        if right_children[node] == self.NIL:
            replacement = left_children[node]
        elif left_children[node] == self.NIL:
            replacement = right_children[node]
        else:
            replacement = left_children[node]
            if right_children[replacement] != self.NIL:
                while right_children[replacement] != self.NIL:
                    replacement = right_children[replacement]

                right_children[parents[replacement]] = left_children[replacement]
                parents[left_children[replacement]] = parents[replacement]

                left_children[replacement] = left_children[node]
                parents[left_children[node]] = replacement

            right_children[replacement] = right_children[node]
            parents[right_children[node]] = replacement

        parents[replacement] = parents[node]
        if right_children[parents[node]] == node:
            right_children[parents[node]] = replacement
        else:
            left_children[parents[node]] = replacement

        parents[node] = self.NIL
