import re
from abc import ABC, abstractmethod
from typing import Any, Dict, List

import numpy as np
from pcgsepy.config import PL_HIGH, PL_LOW
from pcgsepy.lsystem.actions import Rotations
from pcgsepy.lsystem.rules import StochasticRules


class LParser(ABC):
    def __init__(self,
                 rules: StochasticRules):
        """Create a parser.

        Args:
            rules (StochasticRules): The set of expansion rules.
        """
        self.rules = rules

    @abstractmethod
    def expand(self,
               string: str) -> str:
        """Expand a string using the parser rules.

        Args:
            string (str): The string to expand.

        Returns:
            str: The expanded string.
        """
        pass


class HLParser(LParser):
    def expand(self,
               string: str) -> str:
        i = 0
        while i < len(string):
            offset = 0
            for k in self.rules.lhs_alphabet:
                if string[i:].startswith(k):
                    offset += len(k)
                    lhs = k
                    n = None
                    # check if there are parameters
                    if i + offset < len(string) and string[i + offset] == '(':
                        params = string[i + offset:
                                        string.index(')', i + offset + 1) + 1]
                        offset += len(params)
                        n = int(params.replace('(', '').replace(')', ''))
                        lhs += '(x)'
                        if i + offset < len(string) and string[i + offset] == ']':
                            lhs += ']'
                            offset += 1
                    rhs = self.rules.get_rhs(lhs=lhs)
                    if '(x)' in rhs or '(X)' in rhs or '(Y)' in rhs:
                        # update rhs to include parameters
                        rhs = rhs.replace('(x)', f'({n})')
                        rhs_n = np.random.randint(PL_LOW, PL_HIGH)
                        rhs = rhs.replace('(X)', f'({rhs_n})')
                        if n is not None:
                            rhs = rhs.replace('(Y)', f'({max(1, n - rhs_n)})')
                    string = string[:i] + rhs + string[i + offset:]
                    i += len(rhs) - 1
                    break
            i += 1
        return string


class HLtoMLTranslator:

    def __init__(self,
                 alphabet: Dict[str, Any],
                 tiles_dims: Dict[str, Any],
                 tiles_block_offset: Dict[str, Any]):
        self.alphabet = alphabet
        self.td = tiles_dims
        self.tbo = tiles_block_offset

    def _string_as_list(self,
                        string: str) -> List[Dict[str, Any]]:
        atoms_list = []
        i = 0
        while i < len(string):
            offset = 0
            for k in self.alphabet.keys():
                if string[i:].startswith(k):
                    offset += len(k)
                    n = None
                    # check if there are parameters (multiplicity)
                    if i + offset < len(string) and string[i + offset] == '(':
                        params = string[i + offset:
                                        string.index(')', i + offset + 1) + 1]
                        offset += len(params)
                        n = int(params.replace('(', '').replace(')', ''))
                    atoms_list.append({'atom': k})
                    if k in self.td.keys():
                        atoms_list[-1]['n'] = n if n is not None else 1

                    i += len(k) + (len(f'({n})') if n is not None else 0) - 1
                    break
            i += 1
        return atoms_list

    def _to_midlvl(self,
                   atoms_list: List[Dict[str, Any]]) -> str:
        last_parents = []
        new_string = ''
        rotations = []

        for i, atom in enumerate(atoms_list):
            a = atom['atom']
            n = atom.get('n', None)
            # tile dimensions (either current or parent's)
            if n is None:
                dims = self.td[last_parents[-1]]
            else:
                dims = self.td[a]
            # Translate w/ correction to mid-level
            # Placeable tile
            if a in self.td.keys():
                new = [f"{a}!({dims.y})" for _ in range(n)]
                new_string += ''.join(new)
                # Add closing wall
                if i + 1 < len(atoms_list) and a.startswith(
                        'corridor') and atoms_list[i + 1]['atom'] == ']':
                    new_string += 'corridorwall!(10)'
            # Position stack manipulation
            elif a == '[' or a == ']':
                new_string += a
            # Rotation
            elif a.startswith('Rot'):
                next_tile = None
                for j in range(i + 1, len(atoms_list)):
                    if atoms_list[j].get('n', None) is not None:
                        next_tile = atoms_list[j]['atom']
                        break
                rotations.append(a)
                last_dims = self.td[last_parents[-1]]
                next_dims = self.td[next_tile]
                next_offset = self.tbo[next_tile]
                c = ''
                if a == 'RotZccwX':
                    c = f"+({dims.x})>({next_dims.x - next_offset})"
                elif a == 'RotZcwX':
                    c = f"-({next_offset})"
                elif a == 'RotZcwY':
                    c = f"?({next_offset})"
                elif a == 'RotZccwY':
                    c = f"!({dims.y})>({dims.z -  - next_offset})"
                elif a == 'RotXcwY':
                    c = f"?({next_offset})"
                elif a == 'RotXccwY':
                    c = f"-({next_offset})"
                elif a == 'RotXcwZ':
                    c = f"-({next_offset})"
                elif a == 'RotXccwZ':
                    c = f"+({dims.x})>({next_dims.x - dims.z})"
                elif a == 'RotYcwX':
                    c = f"-({next_offset})"
                elif a == 'RotYccwX':
                    c = f'+({dims.x})!({dims.x - next_offset})'
                elif a == 'RotYcwZ':
                    c = f'>({next_offset})'
                elif a == 'RotYccwZ':
                    c = f"!({dims.z - next_offset})<({last_dims.z})"
                new_string += ''.join([c, a])

            # Parent's stack manipulation
            if i + 1 < len(atoms_list):
                if a != ']' and atoms_list[i + 1]['atom'] == '[':
                    last_parents.append(a)
                if a == ']' and atoms_list[i + 1].get('n', None) is not None:
                    last_parents.pop(-1)

        return new_string

    def _add_intersections(self,
                           string: str) -> str:
        brackets = []
        for i, c in enumerate(string):
            if c == '[':
                # find first closing bracket
                idx_c = string.index(']', i)
                # update closing bracket position in case of nested brackets
                ni_o = string.find('[', i + 1)
                while ni_o != -1 and string.find('[', ni_o) < idx_c:
                    idx_c = string.index(']', idx_c + 1)
                    ni_o = string.find('[', ni_o + 1)
                # add to list of brackets
                brackets.append((i, idx_c))
        to_add = {}
        # add intersection types
        for i, b in enumerate(brackets):
            # get rotation
            for r in [x.value for x in Rotations]:
                if string.find(r, b[0], b[1]) != -1:
                    rot = r
                    break
            # check for neighboring rotations
            has_neighbours = False
            for t0, t1 in brackets[i:]:
                if b[1] == t0 - 1:
                    has_neighbours = True
                    if b[1] not in to_add.keys():
                        to_add[t1] = [rot]
                    else:
                        to_add[t1] = [*to_add[b[1]], rot]
                        to_add.pop(b[1])
                    break
            if not has_neighbours:
                if b[1] not in to_add:
                    to_add[b[1]] = [rot]
                else:
                    to_add[b[1]].append(rot)
        # add to the string
        offset = 0
        for i in sorted(list(to_add.keys())):
            rot = ''.join(sorted(list(set(to_add[i]))))
            s = f'{rot}intersection!(25)'
            string = string[:i + 1 + offset] + s + string[i + 1 + offset:]
            offset += len(s)
        return string

    def transform(self,
                  string: str) -> str:
        atoms_list = self._string_as_list(string)
        try:
            new_string = self._to_midlvl(atoms_list)
            new_string = self._add_intersections(new_string)
        except Exception as e:
            print(string)
            raise e
        return new_string


class LLParser(LParser):
    def expand(self,
               string: str) -> str:
        i = 0
        while i < len(string):
            for k in reversed(list(self.rules.lhs_alphabet)):
                if string[i:].startswith(k):
                    rhs = self.rules.get_rhs(lhs=k)
                    string = string[:i] + rhs + string[i + len(k):]
                    i += len(rhs) - 1
                    break
            i += 1
        return string
