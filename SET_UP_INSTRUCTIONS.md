# Setup Instructions for `agent_cli_r5.py`

Concept for this agent is to allow a user to:

1. Use this in conjunction with Open AI API services
2. Allow Open AI to read your local files and provide responses in relation to said files. See "explicit command line explinations:" below on how to achive this.
3. Allow the API returned code and/or text to be written toe a new file or overwrite and existing file. See the "apply" function below in "explicit command line explinations:"
4. User can open a browser either on the local machine @ http://localhost:7860 or across your local network from another pc by accessing the local machine's url using its IP address on the LAN followed by the port. 
		Example: Your laptop you are runing the code service is 10.158.12.173. From another laotp on the same network in your browser put http://10.158.12.173:7860 in the url line to access the feature remotely. 
5. Below are step by step instructions assuming that you aleady have python3 installed on your Ubuntu machine. The virtual environment is required to run the program as additional pip installed Python  
dependencies are requred to be inbstalled and Ubuntu protects you from installing additional python items sice it relies on Python to function from the OS level.  

## Directory structure

To allow the code to have access to files on your local you will need to place the agent_cli_r5.py and the .env file in the top level of your repository. For example, your project is located in the following directory:

/home/ricky_bobby/Documents/ass_blaster_1000

This way it protects the rest of your repos from accidentally overwritten and hte code cannot write above this directory. 

Then if you want to add folders inside the ass_blaster_1000 folder for reading and writing by the agent if desired, Example

```/home 
└──ricky_bobby
	└──Documents
		└──ass_blaster_1000
				agent_cli_r5.py
			    .env
			└──folder_1
			└──folder_2
```

Each time you start the agent program you will need to specify either the sub-folders you want it ot have access to, or individual files. See the "explicit command line explinations:" how to accomplish this. 

## 1. Setting up Python and Virtual Environment

Install Python and necessary tools:

```bash
sudo apt update
sudo apt install python3 python3-pip python3-venv -y
```

Create a virtual environment in your project folder:

```bash
python3 -m venv myenv
```

## 2. Install Required Dependencies

Activate your virtual environment and install the required packages:

```bash
source myenv/bin/activate
pip install Flask openai python-dotenv difflib pypdf
```

## 3. Activate Virtual Environment

To start using the virtual environment:

```bash
source myenv/bin/activate
```

## 4. Start the `agent_cli_r5.py` Script

Ensure the script is executable:

```bash
chmod +x agent_cli_r5.py
```

Run the script from within the virtual environment:

```bash
./agent_cli_r5.py
```

## 5. Accessing the Program via Browser on the local machine.

After starting the script, navigate your browser to:

```
http://localhost:7860
```

## 6. Allowing File Access

### From UI

- Navigate to the 'Allowed' section in the UI and add paths of directories or files you want the program to access.

### From Command Line

- Use the command `/add /path/to/your/directory` or `/add /path/to/your/file` in the terminal interface of the program to add allowed paths or files.

- To view allowed directories and files, use the `/allowed` command in the terminal interface.

#### explicit command line explinations:

1. **/help**
   - **Explanation:** Displays a list of available commands that a user can execute in the command-line interface (CLI). This command helps users understand which operations they can perform using the AI agent.

2. **/add <file>**
   - **Explanation:** Adds a single file to the context of the AI agent, allowing the agent to read its contents and include it in the processing context. This is useful for specifying specific files that the AI should consider when generating a response or performing operations.

3. **/add-dir <dir>**
   - **Explanation:** Adds all readable files from a specified directory to the AI agent’s processing context. This command recursively navigates through the directory and includes all files that match predefined readable extensions while avoiding any `.git` directories. It's beneficial for including whole directories in the context rather than adding files one by one.

4. **/files**
   - **Explanation:** Lists all the files that are currently loaded into the AI agent's context. This command allows users to see which files the AI is considering in its current operational context.

5. **/allowed**
   - **Explanation:** Displays a list of directories and files that are allowed to be accessed by the AI agent. This is essential for security and management, ensuring that the agent operates within predefined constraints.

6. **/diff**
   - **Explanation:** Shows a unified diff of changes that are pending application by comparing the current content of files with the changes suggested by the AI. This command aids in reviewing what modifications the AI plans to make before they are implemented.

7. **/apply**
   - **Explanation:** Applies all pending changes that the AI agent has proposed. Typically, this command executes after reviewing differences using the `/diff` command and when the user confirms the desire to make those changes. It's a crucial step in the workflow of applying AI suggestions to modify files directly.

8. **/quit**
   - **Explanation:** Exits the command-line interface. This command helps in properly closing the CLI environment and ensuring that all temporary operational contexts are cleared.

These commands facilitate various functionalities of the AI assistant, helping users manage files, view changes, and interact securely and effectively with the artificial intelligence agent within specified permissions and contexts.
