from .base import Tool, ToolContext, ToolError
from .fs import TOOLS as FS_TOOLS
from .shell import TOOLS as SHELL_TOOLS
from .unit import TOOLS as UNIT_TOOLS

ALL_TOOLS: list[Tool] = [*FS_TOOLS, *SHELL_TOOLS, *UNIT_TOOLS]

# tools a dispatched support unit gets: look, never touch
SUBAGENT_TOOLS = {"read_file", "list_dir", "find_files", "search", "recall"}

# self-management tools that skip the approval prompt in ask mode (still logged, still denied in observe)
BUILTIN_ALLOW = ["remember(*)", "todo(*)", "propose_skill(*)"]

__all__ = ["Tool", "ToolContext", "ToolError", "ALL_TOOLS", "SUBAGENT_TOOLS", "BUILTIN_ALLOW"]
