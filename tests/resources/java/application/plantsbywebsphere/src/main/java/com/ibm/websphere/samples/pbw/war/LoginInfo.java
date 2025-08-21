//
// COPYRIGHT LICENSE: This information contains sample code provided in source code form. You may copy, 
// modify, and distribute these sample programs in any form without payment to IBM for the purposes of 
// developing, using, marketing or distributing application programs conforming to the application 
// programming interface for the operating platform for which the sample code is written. 
// Notwithstanding anything to the contrary, IBM PROVIDES THE SAMPLE SOURCE CODE ON AN "AS IS" BASIS 
// AND IBM DISCLAIMS ALL WARRANTIES, EXPRESS OR IMPLIED, INCLUDING, BUT NOT LIMITED TO, ANY IMPLIED 
// WARRANTIES OR CONDITIONS OF MERCHANTABILITY, SATISFACTORY QUALITY, FITNESS FOR A PARTICULAR PURPOSE, 
// TITLE, AND ANY WARRANTY OR CONDITION OF NON-INFRINGEMENT. IBM SHALL NOT BE LIABLE FOR ANY DIRECT, 
// INDIRECT, INCIDENTAL, SPECIAL OR CONSEQUENTIAL DAMAGES ARISING OUT OF THE USE OR OPERATION OF THE 
// SAMPLE SOURCE CODE. IBM HAS NO OBLIGATION TO PROVIDE MAINTENANCE, SUPPORT, UPDATES, ENHANCEMENTS 
// OR MODIFICATIONS TO THE SAMPLE SOURCE CODE.  
//
// (C) COPYRIGHT International Business Machines Corp., 2003,2011
// All Rights Reserved * Licensed Materials - Property of IBM
//

package com.ibm.websphere.samples.pbw.war;

import javax.validation.constraints.Pattern;
import javax.validation.constraints.Size;

/**
 * A JSF backing bean used to store information for the login web page. It is accessed via the
 * account bean.
 *
 */
public class LoginInfo {
	private String checkPassword;

	@Pattern(regexp = "[a-zA-Z0-9_-]+@[a-zA-Z0-9.-]+")
	private String email;
	private String message;

	@Size(min = 6, max = 10, message = "Password must be between 6 and 10 characters.")
	private String password;

	public LoginInfo() {
	}

	public String getCheckPassword() {
		return this.checkPassword;
	}

	public String getEmail() {
		return this.email;
	}

	public String getMessage() {
		return this.message;
	}

	public String getPassword() {
		return this.password;
	}

	public void setCheckPassword(String checkPassword) {
		this.checkPassword = checkPassword;
	}

	public void setEmail(String email) {
		this.email = email;
	}

	public void setMessage(String message) {
		this.message = message;
	}

	public void setPassword(String password) {
		this.password = password;
	}
}
