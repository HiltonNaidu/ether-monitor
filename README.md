# ether-monitor
a module focused on the management and WoL of multiple devices on an network 

## Usage 
ethmon [macaddress, allias]                 --> lists any avaliable infomation, and pings to check activity 
ethmon add [macaddress]                      --> adds that macadress to devices but does not have alias 
ethmon alias [macaddress, alias] [alias]    --> assigns a alias to a defined macaddress  
ethmon monitor [macaddress, alias, "all"]   --> lists macaddress, alias, ping infomation, ip address if avaliable 
ethmon wake [macaddress, alias, "all"]      --> sends wake on lan magic packet 
ethmon list                                 --> lists all macaddresses and their alias 
ethmon ping [macaddress, alias, "all"]      --> pings that macadress 
ethmon scan                                 ---> checks for devices on the network 
