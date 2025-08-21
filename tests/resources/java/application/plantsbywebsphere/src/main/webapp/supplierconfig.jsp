<!DOCTYPE HTML PUBLIC "-//W3C//DTD HTML 4.01 Transitional//EN">
<!--

COPYRIGHT LICENSE: This information contains sample code provided in source code form. You may copy, 
modify, and distribute these sample programs in any form without payment to IBM for the purposes of 
developing, using, marketing or distributing application programs conforming to the application 
programming interface for the operating platform for which the sample code is written. 
Notwithstanding anything to the contrary, IBM PROVIDES THE SAMPLE SOURCE CODE ON AN "AS IS" BASIS 
AND IBM DISCLAIMS ALL WARRANTIES, EXPRESS OR IMPLIED, INCLUDING, BUT NOT LIMITED TO, ANY IMPLIED 
WARRANTIES OR CONDITIONS OF MERCHANTABILITY, SATISFACTORY QUALITY, FITNESS FOR A PARTICULAR PURPOSE, 
TITLE, AND ANY WARRANTY OR CONDITION OF NON-INFRINGEMENT. IBM SHALL NOT BE LIABLE FOR ANY DIRECT, 
INDIRECT, INCIDENTAL, SPECIAL OR CONSEQUENTIAL DAMAGES ARISING OUT OF THE USE OR OPERATION OF THE 
SAMPLE SOURCE CODE. IBM HAS NO OBLIGATION TO PROVIDE MAINTENANCE, SUPPORT, UPDATES, ENHANCEMENTS 
OR MODIFICATIONS TO THE SAMPLE SOURCE CODE.  

(C) COPYRIGHT International Business Machines Corp., 2004,2011
All Rights Reserved * Licensed Materials - Property of IBM

-->
<html>
<head>
<%@ page language="java" contentType="text/html; charset=ISO-8859-1"
	pageEncoding="ISO-8859-1"%>
<meta http-equiv="Content-Type" content="text/html; charset=ISO-8859-1">
<meta name="GENERATOR" content="IBM WebSphere Studio">
<meta http-equiv="Content-Style-Type" content="text/css">
<link href="/PlantsByWebSphere/PlantMain.css" rel="stylesheet"
	type="text/css">
<title>Supplier Configuration</title>

<script type="text/javascript" src="/PlantsByWebSphere/applycss.js"
	language="JavaScript"></script>
<script language="JavaScript" src="/PlantsByWebSphere/collectionform.js"></script>
<script>
	function verifyForm(supplier) {
		if ((!exists(supplier.name.value)) || (!exists(supplier.street.value))
				|| (!exists(supplier.city.value))
				|| (!exists(supplier.state.value))
				|| (!exists(supplier.zip.value))
				|| (!exists(supplier.phone.value))
				|| (!exists(supplier.location_url.value))) {
			alert("All required fields must be filled in.");
			return false;
		} else if (!verifyNum(supplier.zip.value)) {
			alert("Supplier Zip Code is not valid.");
			return false;
		} else if (!verifyPhone(supplier.phone.value)) {
			alert("Supplier Phone is not valid.");
			return false;
		}
		return true;
	}

	function exists(inputVal) {
		var result = false;
		for (var i = 0; i <= inputVal.length; i++) {
			if ((inputVal.charAt(i) != " ") && (inputVal.charAt(i) != "")) {
				result = true;
				break;
			}
		}
		return result;
	}

	function verifyNum(numVal) {
		var result = false;
		for (var i = 0; i < numVal.length; i++) {
			if (parseFloat(numVal.charAt(i))) {
				result = true;
				break;
			}
		}
		return result;
	}

	function verifyPhone(phoneVal) {
		var result = false;
		var cnt = 0;
		for (var i = 0; i < phoneVal.length; i++) {
			if (parseFloat(phoneVal.charAt(i))) {
				cnt++;
				if (cnt >= 7) {
					result = true;
					break;
				}
			}
		}
		return result;
	}
</script>

</head>
<body class="adminactions" marginwidth="0" leftmargin="0">

	<%@page
		import="com.ibm.websphere.samples.pbw.jpa.Supplier,,com.ibm.websphere.samples.pbw.utils.Util,java.util.*"
		session="true" isThreadSafe="true" isErrorPage="false"%>
	<%
		com.ibm.websphere.samples.pbw.jpa.Supplier supplierInfo = (com.ibm.websphere.samples.pbw.jpa.Supplier) session
				.getAttribute(com.ibm.websphere.samples.pbw.utils.Util.ATTR_SUPPLIER);
		String id = "";
		String name = "";
		String street = "";
		String city = "";
		String state = "";
		String zip = "";
		String phone = "";
		String url = "";
		if (supplierInfo != null) {
			id = supplierInfo.getSupplierID();
			name = supplierInfo.getName();
			street = supplierInfo.getStreet();
			city = supplierInfo.getCity();
			state = supplierInfo.getUsstate();
			zip = supplierInfo.getZip();
			phone = supplierInfo.getPhone();
			url = supplierInfo.getUrl();
		}
	%>

	<table border="0" cellpadding="4" cellspacing="0" width="100%">
		<tbody>
			<tr>
				<td class="trail">
					<p class="trail">
						<a class="trail" class="footer"
							href="/PlantsByWebSphere/adminactions.html" target="adminactions">Admin
							Home</a>
					</p>
				</td>
			</tr>
			<tr>
				<td width="100%">
					<table cellpadding="5" cellspacing="5" border="0" width="600">
						<tbody>
							<tr>
								<td width="100%" valign="middle">
									<h1>Supplier Configuration</h1>
								</td>
								<td align="right" valign="middle" nowrap></td>
								<td valign="middle"></td>
							</tr>
						</tbody>
					</table> <br>
					<form onsubmit="return verifyForm(this);" target="_self"
						name="supplier" action="/PlantsByWebSphere/servlet/AdminServlet"
						method="post">
						<table border="0" cellpadding="0" cellspacing="0">
							<caption>Enter the Supplier's Configuration Information</caption>
							<colgroup class="label">
							</colgroup>
							<colgroup>
							</colgroup>
							<tbody>
								<tr>
									<td></td>
									<td><input type="hidden" name="supplierid" value="<%=id%>"></td>
								</tr>
								<tr>
									<td nowrap width="120">
										<p>
											<label for="name">Full Name&nbsp;</label>
										</p>
									</td>
									<td width="100%">
										<p>
											<input type="text" name="name" size="60" maxlength="250"
												value="<%=name%>">
										</p>
									</td>
								</tr>
								<tr>
									<td nowrap width="120">
										<p>
											<label for="street">Street Address&nbsp;</label>
										</p>
									</td>
									<td width="100%">
										<p>
											<input type="text" name="street" size="60" maxlength="250"
												value="<%=street%>">
										</p>
									</td>
								</tr>
								<tr>
									<td nowrap width="120">
										<p>
											<label for="city">City&nbsp;</label>
										</p>
									</td>
									<td width="100%">
										<p>
											<input type="text" name="city" size="60" maxlength="250"
												value="<%=city%>">
										</p>
									</td>
								</tr>
								<tr>
									<td nowrap width="120">
										<p>
											<label for="state">State&nbsp;</label>
										</p>
									</td>
									<td width="100%">
										<p>
											<input type="text" name="state" size="20" maxlength="250"
												value="<%=state%>">
										</p>
									</td>
								</tr>
								<tr>
									<td nowrap width="120">
										<p>
											<label for="zip">Zip&nbsp;</label>
										</p>
									</td>
									<td width="100%">
										<p>
											<input type="text" name="zip" size="20" maxlength="250"
												value="<%=zip%>">
										</p>
									</td>
								</tr>
								<tr>
									<td nowrap width="120">
										<p>
											<label for="phone">Phone&nbsp;</label>
										</p>
									</td>
									<td width="100%">
										<p>
											<input type="text" name="phone" size="20" maxlength="250"
												value="<%=phone%>">
										</p>
									</td>
								</tr>
								<tr>
									<td nowrap width="120">
										<p>
											<label for="location_url">Location URL&nbsp;</label>
										</p>
									</td>
									<td width="100%">
										<p>
											<input type="text" name="location_url" size="60"
												maxlength="250" value="<%=url%>">
										</p>
									</td>
								</tr>
							</tbody>
						</table>
						<input type="submit" name="updateconfig"
							value="Update Configuration"> <input type="hidden"
							name="admintype" value="<%=Util.ADMIN_SUPPLIERCFG%>"> <input
							type="hidden" name="action"
							value="<%=Util.ACTION_UPDATESUPPLIER%>">
					</form>
				</td>
			</tr>
		</tbody>
	</table>
	<table bgcolor="#669966" border="0" cellpadding="0" cellspacing="0"
		width="100%">
		<tbody>
			<tr>
				<td width="1"><img border="0"
					src="resources/images/1x1_trans.gif" width="1" height="1" alt=""></td>
			</tr>
		</tbody>
	</table>
	<p>
		<br>
	</p>
	<table border="0" cellpadding="5" cellspacing="0" width="100%">
		<tbody>
			<tr>
				<td><img src="resources/images/poweredby_WebSphere.gif"
					alt="Powered by WebSphere"></td>
				<td>
					<p class="footer">
						<a class="footer" href="/PlantsByWebSphere/index.html"
							target="_top">Home</a>&nbsp;&nbsp;:&nbsp; <a class="footer"
							href="/PlantsByWebSphere/admin.html" target="_top">Admin Home</a>&nbsp;&nbsp;:&nbsp;
						<a class="footer" href="help.jsf" target="_blank">Help</a>
					</p>
				</td>
			</tr>
		</tbody>
	</table>
</body>
</html>
