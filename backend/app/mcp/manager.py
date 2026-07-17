import os
import logging
from contextlib import AsyncExitStack
from mcp import ClientSession
from mcp.client.stdio import stdio_client, StdioServerParameters

logger = logging.getLogger(__name__)

class MCPManager:
    """Manages Model Context Protocol servers."""
    
    def __init__(self):
        self.active_sessions = {}
        self.exit_stacks = {}
        
    async def start_google_drive_mcp(self, access_token: str):
        """Spawns the Google Drive MCP server and initializes the MCP session."""
        logger.info("Starting Google Drive MCP Server...")
        
        env = os.environ.copy()
        # Securely pass the access token to the subprocess via environment variables
        env["GOOGLE_ACCESS_TOKEN"] = access_token
        
        server_params = StdioServerParameters(
            command="npx",
            args=["-y", "@modelcontextprotocol/server-google-drive"],
            env=env
        )
        
        exit_stack = AsyncExitStack()
        
        try:
            # Connect to the stdio transport
            read_stream, write_stream = await exit_stack.enter_async_context(
                stdio_client(server_params)
            )
            
            # Initialize the client session
            session = await exit_stack.enter_async_context(
                ClientSession(read_stream, write_stream)
            )
            
            # Initialize the protocol
            await session.initialize()
            
            # Store references to keep the session alive
            self.active_sessions["google_drive"] = session
            self.exit_stacks["google_drive"] = exit_stack
            
            logger.info("Successfully initialized Google Drive MCP Server via stdio.")
            
            # Fetch the available tools to log them
            tools_result = await session.list_tools()
            
            logger.info("====== AVAILABLE MCP TOOLS ======")
            for tool in tools_result.tools:
                logger.info(f"Tool: {tool.name}")
                logger.info(f"Description: {tool.description}")
                logger.info("-" * 30)
                
            return session
            
        except Exception as e:
            logger.error(f"Failed to start Google Drive MCP Server: {e}")
            await exit_stack.aclose()
            raise

# Global instance
mcp_manager = MCPManager()
